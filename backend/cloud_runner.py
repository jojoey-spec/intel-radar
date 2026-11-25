"""Zero-dependency Cloud Runner for GitHub Actions (Batch GitHub Models API + State Persistence)."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .analyzer import (
    DEAL_NOISE_RE,
    OFFICIAL_CONFIRMED_RE,
    RUMOR_KEYWORDS_RE,
    VALID_DOMAINS,
    _calibrate_major_score,
    _calibrate_rumor_credibility,
    _extract_json_dict,
)
from .config import DEFAULT_FEEDS, MAJOR_ARCHIVE_MIN_LEVEL, MAJOR_NOTIFY_MIN_LEVEL, RUMOR_LOW_MAX, RUMOR_WATCH_MAX, WEB_DIR
from .fetcher import cluster_items, fetch_all_feeds
from .notifier import dispatch_alert

DEFAULT_LLM_URL = "http://127.0.0.1:11434/v1/chat/completions"
DEFAULT_CLOUD_MODEL = "qwen3:4b-instruct"
MAX_CLUSTERS_PER_BATCH = 8
MAX_SEEN_HASHES = 3500

BATCH_SYSTEM_PROMPT = """你是一個極度嚴謹、零幻覺的「全球情報與傳言批次判讀守門員」。
輸入是一組編號的新聞/爆料叢集列表，請針對每一筆依序判讀，並輸出單一合法 JSON 物件：{"results": [ ... ]}，絕不輸出多餘文字。

【鐵律：防幻覺與嚴格分流】
1. 嚴禁編造原文未提及的事實、人名、數據或因果。標題與摘要必須使用「台灣繁體中文慣用詞彙」（如：川普、晶片、記憶體、筆電、伺服器、執行長、購併；專有名詞：Jensen Huang=黃仁勳、Riemann Hypothesis=黎曼猜想、Generalized Riemann Hypothesis=廣義黎曼猜想、Sam Altman=奧特曼、Elon Musk=馬斯克、Tim Cook=庫克、TSMC=台積電，嚴禁簡體字）。
2. `kind` 三選一：
   - `"noise"`：促銷特價、購物指南、開箱評測、影集續訂、遊戲小更新、名人嘴砲、純評論專欄、一般地方瑣事。
   - `"rumor"`：**尚未正式官宣或未證實**的未來產品規格流出、秘密談判、供應鏈風聲、爆料客說法、FCC/跑分庫洩漏。（已正式宣布 Announces/Launches 的新聞絕不可判為 rumor！）
   - `"major"`：**已確認發生**且具備產業級或世界級實質影響力的真實大事。
3. 若 `kind == "major"`：
   - `impact_scope`：`"world_or_industry_shaking"`（僅限核彈級本體事件：科技巨頭CEO閃電更迭/復職本體、市值單日兆元級劇變、宣戰/停火/重大軍事打擊、金融危機/緊急救市、重大晶片禁令、顛覆性科學數學突破） | `"major_sector_event"`（重要產業/政策/財報/新品動態） | `"routine_news"`
   - `event_type`：`"ceo_or_titan_shock"` | `"war_or_geopolitics_crisis"` | `"macro_financial_shock"` | `"paradigm_breakthrough"` | `"major_product_or_policy"` | `"ordinary"`
4. 若 `kind == "rumor"`（依客觀證據選，禁止亂猜機率）：
   - `source_credibility`：`"hard_leak_artifact"`（官網誤發/程式碼/FCC認證/跑分庫硬證據） | `"tier1_investigative"`（彭博/路透/WSJ獨家引述） | `"known_insider_track_record"`（知名爆料客/專業媒體引述供應鏈） | `"single_unverified_or_anonymous"`（單一匿名貼文/未知來源） | `"contradictory_or_clickbait"`（誇大臆測）
   - `detail_specificity`：`"concrete_verifiable_details"` | `"moderate_details"` | `"vague_speculation"`

【輸出格式（每個元素必須帶回對應的 `idx`）】
{
  "results": [
    {
      "idx": 0,
      "kind": "major" | "rumor" | "noise",
      "domain": "AI・科技巨頭" | "半導體・晶片" | "國際政經" | "金融市場" | "Apple・供應鏈" | "科學・能源" | "遊戲・娛樂爆料",
      "title_zh": "精煉台灣繁體中文標題（12到30字）",
      "summary_zh": "台灣繁體中文客觀摘要（1到2句話）",
      "impact_scope": "world_or_industry_shaking" | "major_sector_event" | "routine_news",
      "event_type": "ceo_or_titan_shock" | "war_or_geopolitics_crisis" | "macro_financial_shock" | "paradigm_breakthrough" | "major_product_or_policy" | "ordinary",
      "source_credibility": "hard_leak_artifact" | "tier1_investigative" | "known_insider_track_record" | "single_unverified_or_anonymous" | "contradictory_or_clickbait",
      "detail_specificity": "concrete_verifiable_details" | "moderate_details" | "vague_speculation",
      "reasoning_zh": "一句話說明判讀依據（25字內）"
    }
  ]
}"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def rumor_tier_for(pct: int) -> str:
    if pct <= RUMOR_LOW_MAX:
        return "unlikely"
    if pct <= RUMOR_WATCH_MAX:
        return "watch"
    return "likely"


_TIER_RANK = {"": 0, "unlikely": 1, "watch": 2, "likely": 3}


def call_github_models_batch(clusters: List[Dict[str, Any]], token: str, model: str = DEFAULT_CLOUD_MODEL) -> Dict[int, Dict[str, Any]]:
    """Evaluate up to 14 clusters in a SINGLE HTTP request to stay well inside free RPD limits."""
    lines = []
    for idx, cl in enumerate(clusters):
        sources = cl.get("sources", [])
        s_names = ", ".join(s["name"] for s in sources[:5])
        lines.append(
            f"[{idx}] 來源：{s_names} (獨立信源數:{cl.get('source_count', 1)}, 一線外媒數:{cl.get('tier1_count', 0)}, HN熱度:{cl.get('hn_points', 0)})\n"
            f"    標題：{cl['lead_title']}\n"
            f"    摘要：{cl.get('lead_summary', '')[:320] or '（無額外摘要）'}"
        )

    user_content = "請依序判讀以下情報叢集並回傳單一 JSON 物件：\n\n" + "\n\n".join(lines)

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.0,
        "max_tokens": 3600,
        "response_format": {"type": "json_object"},
    }

    llm_url = (os.environ.get("LLM_BASE_URL") or DEFAULT_LLM_URL).strip()
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "User-Agent": "IntelRadar-CloudRunner/0.2",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        llm_url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=240) as resp:
        raw_resp = json.loads(resp.read().decode("utf-8", errors="ignore"))

    content = (
        ((raw_resp.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
    )
    parsed = _extract_json_dict(content) or {}
    items = parsed.get("results") if isinstance(parsed.get("results"), list) else []
    out: Dict[int, Dict[str, Any]] = {}
    for entry in items:
        if isinstance(entry, dict) and isinstance(entry.get("idx"), int):
            out[entry["idx"]] = entry
    return out


def load_json_safe(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def run_cloud_update(prev_state_path: Path, prev_meta_path: Path, out_state_path: Path, out_meta_path: Path) -> None:
    token = (os.environ.get("MODELS_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    model = (os.environ.get("CLOUD_MODEL") or DEFAULT_CLOUD_MODEL).strip()

    prev_state = load_json_safe(prev_state_path, {})
    prev_meta = load_json_safe(prev_meta_path, {"seen_hashes": [], "items": {}, "noise_filtered_total": 0, "evaluated_total": 0})

    seen_hashes: List[str] = list(prev_meta.get("seen_hashes") or [])
    seen_set = set(seen_hashes)
    items_map: Dict[str, Dict[str, Any]] = dict(prev_meta.get("items") or {})
    auto_history: List[Dict[str, Any]] = list(prev_state.get("auto_history") or [])

    # Seed items_map from prev_state if meta was empty (smooth migration from Mac-Primary state.json!)
    if not items_map and prev_state:
        for bucket in (
            prev_state.get("major_alerts") or [],
            prev_state.get("major_archived") or [],
            (prev_state.get("rumors") or {}).get("likely") or [],
            (prev_state.get("rumors") or {}).get("watch") or [],
            (prev_state.get("rumors") or {}).get("unlikely") or [],
        ):
            for it in bucket:
                fp = it.get("fingerprint")
                if fp:
                    items_map[fp] = it
                    for s in it.get("sources") or []:
                        if s.get("url"):
                            h = hashlib.sha1(s["url"].strip().split("#")[0].encode("utf-8")).hexdigest()[:20]
                            if h not in seen_set:
                                seen_set.add(h)
                                seen_hashes.append(h)

    ts = now_iso()
    all_fetched = fetch_all_feeds(DEFAULT_FEEDS)
    fresh = [it for it in all_fetched if it["url_hash"] not in seen_set]

    # Filter obvious deal noise deterministically before hitting the API
    non_deal_fresh = []
    deal_noise_count = 0
    for it in fresh:
        if DEAL_NOISE_RE.search(it["title"]):
            deal_noise_count += 1
            seen_set.add(it["url_hash"])
            seen_hashes.append(it["url_hash"])
        else:
            non_deal_fresh.append(it)

    clusters = cluster_items(non_deal_fresh)
    major_cls = [c for c in clusters if c.get("stream") != "rumor"]
    rumor_cls = [c for c in clusters if c.get("stream") == "rumor"]
    half = MAX_CLUSTERS_PER_BATCH // 2
    eval_slice = major_cls[:half] + rumor_cls[:half]
    if len(eval_slice) < MAX_CLUSTERS_PER_BATCH:
        used = {c["fingerprint"] for c in eval_slice}
        eval_slice.extend([c for c in clusters if c["fingerprint"] not in used][: MAX_CLUSTERS_PER_BATCH - len(eval_slice)])

    notify_settings = {
        "notify_macos": "false",
        "ntfy_topic": os.environ.get("NTFY_TOPIC", ""),
        "bark_url": os.environ.get("BARK_URL", ""),
        "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
    }

    batch_stats = {"major_alerts": 0, "archived_major": 0, "rumors_added": 0, "noise_filtered": deal_noise_count}

    if eval_slice:
        print(f"[CloudRunner] Calling LLM ({model}) for {len(eval_slice)} new clusters in 1 batch request...")
        ai_map = call_github_models_batch(eval_slice, token=token, model=model)

        for idx, cl in enumerate(eval_slice):
            parsed = ai_map.get(idx, {})
            title = cl["lead_title"]
            summary = cl.get("lead_summary", "")
            sources = cl.get("sources", [])
            source_count = int(cl.get("source_count", max(1, len(sources))))
            tier1_count = int(cl.get("tier1_count", 0))
            hn_points = int(cl.get("hn_points", 0))

            kind = parsed.get("kind", "noise")
            if kind not in ("major", "rumor", "noise"):
                kind = "noise"
            if kind == "major" and RUMOR_KEYWORDS_RE.search(title) and not OFFICIAL_CONFIRMED_RE.search(title):
                kind = "rumor"
            elif kind == "rumor" and OFFICIAL_CONFIRMED_RE.search(title) and not RUMOR_KEYWORDS_RE.search(title):
                kind = "major" if parsed.get("impact_scope") in ("world_or_industry_shaking", "major_sector_event") else "noise"

            domain = parsed.get("domain") or cl.get("domain", "AI・科技巨頭")
            if domain not in VALID_DOMAINS:
                domain = cl.get("domain", "AI・科技巨頭")

            title_zh = (parsed.get("title_zh") or title).strip()[:60]
            summary_zh = (parsed.get("summary_zh") or summary or title).strip()[:220]
            reasoning_zh = (parsed.get("reasoning_zh") or "").strip()[:80]

            impact_level = 0
            credibility_pct = 0
            tags: List[str] = []

            if kind == "major":
                impact_level, tags = _calibrate_major_score(parsed, source_count, tier1_count, hn_points, False)
                if impact_level < MAJOR_ARCHIVE_MIN_LEVEL:
                    kind = "noise"
            elif kind == "rumor":
                credibility_pct, tags = _calibrate_rumor_credibility(parsed, source_count, tier1_count)

            # Mark cluster URLs seen
            for s in sources:
                h = hashlib.sha1(s["url"].strip().split("#")[0].encode("utf-8")).hexdigest()[:20]
                if h not in seen_set:
                    seen_set.add(h)
                    seen_hashes.append(h)

            if kind == "noise":
                batch_stats["noise_filtered"] += 1
                continue

            fp = cl["fingerprint"]
            prev_item = items_map.get(fp)
            should_notify = False
            upgraded = False

            if prev_item:
                old_urls = {s.get("url") for s in prev_item.get("sources", [])}
                merged_sources = list(prev_item.get("sources", []))
                for s in sources:
                    if s.get("url") not in old_urls:
                        merged_sources.append(s)
                        old_urls.add(s.get("url"))
                new_sc = max(int(prev_item.get("source_count", 1)), len(merged_sources), source_count)
                added_sc = max(0, new_sc - int(prev_item.get("source_count", 1)))
                new_impact = max(int(prev_item.get("impact_level", 0)), impact_level)
                new_pct = max(int(prev_item.get("credibility_pct", 0)), credibility_pct)
                if kind == "rumor" and added_sc > 0:
                    new_pct = min(96, new_pct + added_sc * 12)
                new_tier = rumor_tier_for(new_pct) if kind == "rumor" else ""
                if kind == "rumor" and _TIER_RANK.get(new_tier, 0) > _TIER_RANK.get(prev_item.get("rumor_tier", ""), 0):
                    upgraded = True
                    if "⬆️ 可信度升檔" not in tags:
                        tags.insert(0, "⬆️ 可信度升檔")
                already_notified = bool(prev_item.get("notified"))
                if kind == "major" and new_impact >= MAJOR_NOTIFY_MIN_LEVEL and not already_notified:
                    should_notify = True
                elif kind == "rumor" and upgraded and new_tier == "likely":
                    should_notify = True

                merged_tags = list(dict.fromkeys((prev_item.get("evidence_tags") or []) + tags))
                record = {
                    "fingerprint": fp,
                    "kind": kind,
                    "domain": domain,
                    "title_zh": title_zh,
                    "summary_zh": summary_zh,
                    "raw_title": title,
                    "impact_level": new_impact,
                    "credibility_pct": new_pct,
                    "rumor_tier": new_tier,
                    "upgraded": bool(upgraded or prev_item.get("upgraded")),
                    "evidence_tags": merged_tags,
                    "reasoning_zh": reasoning_zh or prev_item.get("reasoning_zh", ""),
                    "sources": merged_sources,
                    "source_count": new_sc,
                    "notified": bool(already_notified or should_notify),
                    "created_at": prev_item.get("created_at", ts),
                    "updated_at": ts,
                }
            else:
                tier = rumor_tier_for(credibility_pct) if kind == "rumor" else ""
                if kind == "major" and impact_level >= MAJOR_NOTIFY_MIN_LEVEL:
                    should_notify = True
                record = {
                    "fingerprint": fp,
                    "kind": kind,
                    "domain": domain,
                    "title_zh": title_zh,
                    "summary_zh": summary_zh,
                    "raw_title": title,
                    "impact_level": impact_level,
                    "credibility_pct": credibility_pct,
                    "rumor_tier": tier,
                    "upgraded": False,
                    "evidence_tags": tags,
                    "reasoning_zh": reasoning_zh,
                    "sources": sources,
                    "source_count": source_count,
                    "notified": should_notify,
                    "created_at": ts,
                    "updated_at": ts,
                }

            items_map[fp] = record
            if kind == "major":
                if record["impact_level"] >= MAJOR_NOTIFY_MIN_LEVEL:
                    batch_stats["major_alerts"] += 1
                    # Auto-promote nuclear alerts into rolling 1-year chronicle!
                    if not any(h.get("id") == fp for h in auto_history):
                        first_url = (record["sources"][0].get("url") if record["sources"] else "") or "#"
                        auto_history.insert(
                            0,
                            {
                                "id": fp,
                                "date": ts[:7],
                                "year": int(ts[:4]),
                                "funnel_tier": 10 if record["impact_level"] >= 10 else 5,
                                "level_label": f"Lv.{record['impact_level']}",
                                "domain": record["domain"],
                                "title_zh": record["title_zh"],
                                "summary_zh": record["summary_zh"],
                                "impact_zh": record.get("reasoning_zh") or "由雲端情報雷達即時捕捉歸檔之重大事件。",
                                "url": first_url,
                                "auto_captured": True,
                            },
                        )
                else:
                    batch_stats["archived_major"] += 1
            else:
                batch_stats["rumors_added"] += 1

            if should_notify:
                dispatch_alert(record, notify_settings)
    else:
        print("[CloudRunner] 0 new clusters requiring AI evaluation (0 API calls spent).")

    # Prune rolling buffers to keep JSON sleek forever
    all_records = sorted(items_map.values(), key=lambda x: x.get("updated_at", ""), reverse=True)[:180]
    items_map = {r["fingerprint"]: r for r in all_records}
    seen_hashes = seen_hashes[-MAX_SEEN_HASHES:]

    major_alerts = [
        r for r in all_records if r["kind"] == "major" and r.get("impact_level", 0) >= MAJOR_NOTIFY_MIN_LEVEL
    ]
    major_alerts.sort(key=lambda x: (x.get("impact_level", 0), x.get("updated_at", "")), reverse=True)

    major_archived = [
        r
        for r in all_records
        if r["kind"] == "major" and MAJOR_ARCHIVE_MIN_LEVEL <= r.get("impact_level", 0) < MAJOR_NOTIFY_MIN_LEVEL
    ]
    major_archived.sort(key=lambda x: (x.get("impact_level", 0), x.get("updated_at", "")), reverse=True)

    rumors = {"likely": [], "watch": [], "unlikely": []}
    for r in all_records:
        if r["kind"] == "rumor":
            t = rumor_tier_for(int(r.get("credibility_pct", 0)))
            r["rumor_tier"] = t
            rumors[t].append(r)
    for k in rumors:
        rumors[k].sort(key=lambda x: (x.get("credibility_pct", 0), x.get("updated_at", "")), reverse=True)

    noise_total = int(prev_meta.get("noise_filtered_total") or prev_state.get("stats", {}).get("noise_filtered", 0)) + batch_stats["noise_filtered"]
    eval_total = len(all_records) + noise_total
    noise_pct = round((noise_total / eval_total) * 100) if eval_total > 0 else 0

    state_out = {
        "major_alerts": major_alerts[:25],
        "major_archived": major_archived[:55],
        "rumors": {
            "likely": rumors["likely"][:35],
            "watch": rumors["watch"][:45],
            "unlikely": rumors["unlikely"][:35],
        },
        "auto_history": auto_history[:50],
        "stats": {
            "total_seen_urls": len(seen_hashes),
            "total_evaluated": eval_total,
            "noise_filtered": noise_total,
            "noise_ratio_pct": noise_pct,
            "major_alert_count": len(major_alerts),
            "major_archive_count": len(major_archived),
            "rumor_count": sum(len(v) for v in rumors.values()),
        },
        "last_scan": {
            "finished_at": ts,
            "status": "ok",
            "message": f"雲端巡檢完成（新增核彈 {batch_stats['major_alerts']}、收錄 {batch_stats['archived_major']}、傳言 {batch_stats['rumors_added']}、攔截雜訊 {batch_stats['noise_filtered']}）",
        },
        "settings": {
            "major_notify_min": str(MAJOR_NOTIFY_MIN_LEVEL),
            "rumor_low_max": str(RUMOR_LOW_MAX),
            "rumor_watch_max": str(RUMOR_WATCH_MAX),
        },
        "heartbeat": {
            "updated_at": ts,
            "node_name": "GitHub-Cloud",
            "model_id": model.split("/")[-1],
        },
        "static_mode": True,
    }

    meta_out = {
        "seen_hashes": seen_hashes,
        "items": items_map,
        "noise_filtered_total": noise_total,
        "evaluated_total": eval_total,
    }

    out_state_path.parent.mkdir(parents=True, exist_ok=True)
    out_state_path.write_text(json.dumps(state_out, ensure_ascii=False, indent=2), encoding="utf-8")
    out_meta_path.write_text(json.dumps(meta_out, ensure_ascii=False), encoding="utf-8")
    print(f"[CloudRunner] Wrote {out_state_path} ({len(all_records)} active records, {noise_total} noise blocked).")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prev-state", default=str(WEB_DIR / "state.json"))
    parser.add_argument("--prev-meta", default=str(WEB_DIR / "state_meta.json"))
    parser.add_argument("--out-state", default=str(WEB_DIR / "state.json"))
    parser.add_argument("--out-meta", default=str(WEB_DIR / "state_meta.json"))
    args = parser.parse_args()
    run_cloud_update(
        Path(args.prev_state),
        Path(args.prev_meta),
        Path(args.out_state),
        Path(args.out_meta),
    )


if __name__ == "__main__":
    main()
