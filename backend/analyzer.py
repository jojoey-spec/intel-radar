"""Local Apple Silicon MLX Intelligence Filter with Anti-Hallucination Evidence Guardrails."""

import json
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from .config import DEFAULT_MLX_MODEL

_MODEL_LOCK = threading.Lock()
_LOADED_MODEL_ID: Optional[str] = None
_MODEL = None
_TOKENIZER = None

VALID_DOMAINS = [
    "AI・科技巨頭",
    "半導體・晶片",
    "國際政經",
    "金融市場",
    "Apple・供應鏈",
    "科學・能源",
    "遊戲・娛樂爆料",
]

RUMOR_KEYWORDS_RE = re.compile(
    r"\b(rumou?r|rumored|leak|leaked|leaker|allegedly|unconfirmed|insider|"
    r"supply[\s\-]chain|reportedly\s+working\s+on|reportedly\s+planning|"
    r"expected\s+to\s+launch|tipster|geekbench|fcc\s+filing|eec\s+filing|"
    r"prototype|render|dummy\s+unit|roadmap\s+leak|could\s+feature|might\s+get)\b"
    r"|傳言|爆料|傳聞|流出|消息人士稱|小道消息|據傳|外洩|諜照",
    re.I,
)

DEAL_NOISE_RE = re.compile(
    r"\b(black\s+friday|cyber\s+monday|prime\s+day|\$\d+\s+off|record\s+low\s+\$|"
    r"best\s+deals|promo\s+code|discount|gift\s+guide|buying\s+guide|review:|"
    r"hands[\s\-]on:|how\s+to\s+|wallpaper\s+download)\b",
    re.I,
)

OFFICIAL_CONFIRMED_RE = re.compile(
    r"\b(announces|announced|officially\s+launches|brings\s+.+\s+to|releases\s+|"
    r"imposes\s+sanctions|reinstatement)\b",
    re.I,
)

SYSTEM_PROMPT = """你是一個極度嚴謹、零幻覺的「全球情報與傳言判讀守門員」。
你的唯一任務是閱讀輸入的新聞/爆料原始標題與摘要，輸出嚴格合法的單一 JSON 物件，絕不輸出任何多餘文字或 Markdown。

【鐵律：防幻覺與嚴格分流】
1. 嚴禁編造原文未提及的事實、人名、數據或因果。標題與摘要必須使用「台灣繁體中文慣用詞彙」（例如：川普、晶片、記憶體、筆電、伺服器、執行長、購併；常見專有名詞：Jensen Huang=黃仁勳、Riemann Hypothesis=黎曼猜想、Generalized Riemann Hypothesis=廣義黎曼猜想、Sam Altman=奧特曼、Elon Musk=馬斯克、Tim Cook=庫克、TSMC=台積電，嚴禁簡體字），且100%忠於原文。
2. 嚴格區分三種資訊性質 (`kind`) —— 寧可判為 `"noise"`，絕不錯放雜訊或假傳言：
   - `"noise"`（日常雜訊 — 預設優先考慮攔截）：
     * 任何促銷折扣、Black Friday 特價、史低價 ($XX Off)、購物指南、開箱評測、教學文。
     * 影集續訂、遊戲小更新、一般名人嘴砲/搶功發言、純評論分析專欄（如 "How important is...", "What X means for..."）。
     * 一般企業例行財報微幅波動、地方社會新聞、日常小功能更新。
   - `"rumor"`（尚未官宣的小道消息/傳言/未證實爆料）：
     * **必須是「尚未正式公開或官方未證實」的未來產品、秘密談判、內部流出、供應鏈風聲、爆料客說法、FCC/跑分庫提前洩漏**！
     * 注意：若標題明確寫官方「已正式宣布 (Announces / Launches / Brings / Imposes)」，或是針對已發生新聞的事後評論，**絕對不是 `"rumor"`**（請依重要性判為 `"major"` 或 `"noise"`）！
   - `"major"`（已確認發生的重大新聞）：
     * 必須是「已經確認發生」的事實，且具備明確的產業級或世界級實質影響力。

3. 若 `kind == "major"`，請極度嚴格評估 `impact_scope`（95% 新聞都不該是核彈級！）：
   - `"world_or_industry_shaking"`（Lv.9–10 核彈級警報，主動響鈴）：
     僅限歷史級/改變格局的突發本體事件！例如：科技巨頭執行長閃電復辟或下台本體新聞、市值單日兆元級劇變、國家宣戰/停火/重大軍事打擊、央行緊急救市/金融危機、重大晶片禁令制裁、顛覆性科學突破。**注意：事後的周邊評論分析文只能給 `"major_sector_event"` 或 `"routine_news"`，不可重複給核彈級！**
   - `"major_sector_event"`（Lv.7–8 靜音收錄）：重要產業動態、大型企業亮眼財報、重要地緣制裁或軍事衝突進展、重量級 AI 模型正式發布。
   - `"routine_news"`（攔截）：一般新聞。
   - `event_type`: `"ceo_or_titan_shock"` | `"war_or_geopolitics_crisis"` | `"macro_financial_shock"` | `"paradigm_breakthrough"` | `"major_product_or_policy"` | `"ordinary"`

4. 若 `kind == "rumor"`，禁止憑空亂猜機率！請根據原文客觀證據選擇：
   - `source_credibility`:
     - `"hard_leak_artifact"`：硬證據流出（官方網站誤發、程式碼字串挖出、FCC/EEC 認證資料庫、Geekbench 實機跑分紀錄）。
     - `"tier1_investigative"`：彭博(Bloomberg)、路透(Reuters)、WSJ、FT 等一線外媒獨家調查引述知情人士。
     - `"known_insider_track_record"`：知名爆料記者/分析師（如 Mark Gurman, 郭明錤, Tom Henderson 等）或專業科技媒體引述供應鏈消息。
     - `"single_unverified_or_anonymous"`：單一論壇匿名貼文、未知來源傳聞、二手轉述。
     - `"contradictory_or_clickbait"`：明顯誇大、違反常識、標題黨臆測。
   - `detail_specificity`:
     - `"concrete_verifiable_details"`：含具體型號、規格數字、時程或代號。
     - `"moderate_details"`：有方向性描述但細節有限。
     - `"vague_speculation"`：模糊猜測、空泛形容。

【輸出 JSON 格式（必須完全符合以下欄位）】
{
  "kind": "major" | "rumor" | "noise",
  "domain": "AI・科技巨頭" | "半導體・晶片" | "國際政經" | "金融市場" | "Apple・供應鏈" | "科學・能源" | "遊戲・娛樂爆料",
  "title_zh": "精煉台灣繁體中文標題（12到30字，不誇大）",
  "summary_zh": "台灣繁體中文客觀摘要（1到2句話，只講原文重點）",
  "impact_scope": "world_or_industry_shaking" | "major_sector_event" | "routine_news",
  "event_type": "ceo_or_titan_shock" | "war_or_geopolitics_crisis" | "macro_financial_shock" | "paradigm_breakthrough" | "major_product_or_policy" | "ordinary",
  "source_credibility": "hard_leak_artifact" | "tier1_investigative" | "known_insider_track_record" | "single_unverified_or_anonymous" | "contradictory_or_clickbait",
  "detail_specificity": "concrete_verifiable_details" | "moderate_details" | "vague_speculation",
  "reasoning_zh": "一句話說明判讀依據（25字內）"
}"""


def get_model_status() -> Dict[str, Any]:
    return {
        "loaded": _MODEL is not None,
        "model_id": _LOADED_MODEL_ID or DEFAULT_MLX_MODEL,
    }


def ensure_model_loaded(model_id: str = DEFAULT_MLX_MODEL) -> Tuple[Any, Any]:
    global _MODEL, _TOKENIZER, _LOADED_MODEL_ID
    with _MODEL_LOCK:
        if _MODEL is not None and _LOADED_MODEL_ID == model_id:
            return _MODEL, _TOKENIZER
        from mlx_lm import load

        _MODEL, _TOKENIZER = load(model_id)
        _LOADED_MODEL_ID = model_id
        return _MODEL, _TOKENIZER


def _extract_json_dict(raw_text: str) -> Optional[Dict[str, Any]]:
    text = raw_text.strip()
    if "<think>" in text and "</think>" in text:
        text = text.split("</think>", 1)[-1].strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    snippet = text[start : end + 1]
    try:
        return json.loads(snippet)
    except Exception:
        # Clean trailing commas if any
        cleaned = re.sub(r",\s*}", "}", snippet)
        try:
            return json.loads(cleaned)
        except Exception:
            return None


def _calibrate_major_score(
    parsed: Dict[str, Any],
    source_count: int,
    tier1_count: int,
    hn_points: int,
    is_sandbox: bool,
) -> Tuple[int, List[str]]:
    scope = parsed.get("impact_scope", "routine_news")
    etype = parsed.get("event_type", "ordinary")
    tags: List[str] = []

    nuclear_types = {
        "ceo_or_titan_shock": "⚡ 巨頭高層/結構劇變",
        "war_or_geopolitics_crisis": "🌍 重大地緣/國家級事件",
        "macro_financial_shock": "📉 金融/總經黑天鵝",
        "paradigm_breakthrough": "🔬 關鍵技術/科學突破",
    }

    if scope == "world_or_industry_shaking" and etype in nuclear_types:
        level = 9
        tags.append(nuclear_types[etype])
        if source_count >= 3 or tier1_count >= 2 or hn_points >= 400:
            level = 10
        # Guardrail: prevent single non-tier1 blog clickbait from triggering Lv.9 unless sandbox test
        if not is_sandbox and tier1_count == 0 and source_count < 2 and hn_points < 250:
            level = 8
            tags.append("🛡️ 單一非一線信源降級保護")
    elif scope in ("world_or_industry_shaking", "major_sector_event"):
        level = 7
        if etype == "major_product_or_policy":
            tags.append("📌 重要產業/政策動態")
        if source_count >= 3 or tier1_count >= 2 or hn_points >= 250:
            level = 8
    else:
        level = 4

    if source_count >= 2:
        tags.insert(0, f"🔁 {source_count} 家信源共振")
    elif tier1_count >= 1:
        tags.append("🏛️ 一線權威外媒")
    if hn_points >= 150:
        tags.append(f"🔥 HN {hn_points} pts")

    return level, tags


def _calibrate_rumor_credibility(
    parsed: Dict[str, Any],
    source_count: int,
    tier1_count: int,
) -> Tuple[int, List[str]]:
    cred = parsed.get("source_credibility", "single_unverified_or_anonymous")
    spec = parsed.get("detail_specificity", "moderate_details")
    tags: List[str] = []

    base_map = {
        "hard_leak_artifact": (76, "📄 官方殘留/認證/跑分硬證據"),
        "tier1_investigative": (74, "🏛️ 權威外媒獨家引述"),
        "known_insider_track_record": (48, "👤 具紀錄爆料客/供應鏈風聲"),
        "single_unverified_or_anonymous": (22, "❓ 單一未證實/匿名來源"),
        "contradictory_or_clickbait": (10, "⚠️ 誇大臆測/缺乏依據"),
    }
    base_pct, cred_badge = base_map.get(cred, (24, "❓ 來源待查證"))
    tags.append(cred_badge)

    spec_delta = {
        "concrete_verifiable_details": (8, "🔍 含具體規格/時程細節"),
        "moderate_details": (0, ""),
        "vague_speculation": (-10, "🌫️ 描述模糊空泛"),
    }
    delta, spec_badge = spec_delta.get(spec, (0, ""))
    if spec_badge:
        tags.append(spec_badge)

    corroboration_bonus = 0
    if source_count >= 3:
        corroboration_bonus = 20
        tags.insert(0, f"🔁 {source_count} 方獨立消息印證")
    elif source_count == 2:
        corroboration_bonus = 12
        tags.insert(0, "🔁 雙來源交叉印證")
    elif tier1_count >= 1 and cred not in ("tier1_investigative", "hard_leak_artifact"):
        corroboration_bonus = 8

    # Guardrail: contradictory/clickbait cannot exceed 33% even with concrete numbers
    if cred == "contradictory_or_clickbait":
        final_pct = max(5, min(30, base_pct + delta))
    else:
        final_pct = max(8, min(95, base_pct + delta + corroboration_bonus))

    return int(final_pct), tags


def evaluate_cluster(
    cluster: Dict[str, Any],
    model_id: str = DEFAULT_MLX_MODEL,
    is_sandbox: bool = False,
) -> Dict[str, Any]:
    """Evaluate one clustered news/rumor item with the local MLX LLM + deterministic guardrails."""
    title = cluster["lead_title"]
    summary = cluster.get("lead_summary", "")
    sources = cluster.get("sources", [])
    source_names = ", ".join(s["name"] for s in sources[:5]) or "Custom Input"
    source_count = int(cluster.get("source_count", max(1, len(sources))))
    tier1_count = int(cluster.get("tier1_count", 0))
    hn_points = int(cluster.get("hn_points", 0))
    stream_hint = cluster.get("stream", "both")

    # Fast pre-LLM deterministic noise filter for obvious shopping deals / promos / how-to guides
    if not is_sandbox and DEAL_NOISE_RE.search(title):
        return {
            "fingerprint": cluster["fingerprint"],
            "kind": "noise",
            "domain": cluster.get("domain", "AI・科技巨頭"),
            "title_zh": title[:50],
            "summary_zh": summary[:120],
            "raw_title": title,
            "impact_level": 0,
            "credibility_pct": 0,
            "evidence_tags": [],
            "reasoning_zh": "促銷折扣/評測教學自動攔截",
            "sources": sources,
            "source_count": source_count,
            "is_sandbox": is_sandbox,
        }

    user_prompt = (
        f"【來源媒體】：{source_names}（獨立信源數：{source_count}，一線權威媒體數：{tier1_count}，HN熱度：{hn_points}）\n"
        f"【原始標題】：{title}\n"
        f"【原始摘要】：{summary or '（無額外摘要，請僅依標題客觀判斷）'}\n\n"
        f"請嚴格依據系統規則輸出單一 JSON："
    )

    model, tokenizer = ensure_model_loaded(model_id)
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    try:
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
    except TypeError:
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

    with _MODEL_LOCK:
        raw_out = generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=320,
            sampler=make_sampler(temp=0.0),
        )

    parsed = _extract_json_dict(raw_out) or {}

    kind = parsed.get("kind", "noise")
    if kind not in ("major", "rumor", "noise"):
        kind = "noise"

    # Hard keyword guardrail: explicit unconfirmed rumor/leak phrasing must never masquerade as confirmed major news
    if kind == "major" and RUMOR_KEYWORDS_RE.search(title) and not OFFICIAL_CONFIRMED_RE.search(title):
        kind = "rumor"
    elif kind == "rumor" and OFFICIAL_CONFIRMED_RE.search(title) and not RUMOR_KEYWORDS_RE.search(title):
        # Official announcement is not an unconfirmed rumor
        kind = "major" if parsed.get("impact_scope") in ("world_or_industry_shaking", "major_sector_event") else "noise"

    domain = parsed.get("domain") or cluster.get("domain", "AI・科技巨頭")
    if domain not in VALID_DOMAINS:
        domain = cluster.get("domain", "AI・科技巨頭")

    title_zh = (parsed.get("title_zh") or title).strip()[:60]
    summary_zh = (parsed.get("summary_zh") or summary or title).strip()[:220]
    reasoning_zh = (parsed.get("reasoning_zh") or "").strip()[:80]

    impact_level = 0
    credibility_pct = 0
    evidence_tags: List[str] = []

    if kind == "major":
        impact_level, evidence_tags = _calibrate_major_score(
            parsed, source_count, tier1_count, hn_points, is_sandbox
        )
        if impact_level < 7:
            kind = "noise"
    elif kind == "rumor":
        credibility_pct, evidence_tags = _calibrate_rumor_credibility(
            parsed, source_count, tier1_count
        )

    return {
        "fingerprint": cluster["fingerprint"],
        "kind": kind,
        "domain": domain,
        "title_zh": title_zh,
        "summary_zh": summary_zh,
        "raw_title": title,
        "impact_level": impact_level,
        "credibility_pct": credibility_pct,
        "evidence_tags": evidence_tags,
        "reasoning_zh": reasoning_zh,
        "sources": sources,
        "source_count": source_count,
        "is_sandbox": is_sandbox,
    }
