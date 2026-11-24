"""HTTP API server and background scan scheduler for Intel Radar."""

import argparse
import hashlib
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import threading
import time
import urllib.parse
from typing import Any, Dict

from .analyzer import ensure_model_loaded, evaluate_cluster, get_model_status
from .config import DEFAULT_FEEDS, MAX_LLM_EVAL_PER_SCAN, WEB_DIR
from .db import (
    clear_sandbox_items,
    filter_unseen_urls,
    get_settings,
    init_db,
    mark_urls_seen,
    now_iso,
    query_board_state,
    record_scan_finish,
    record_scan_start,
    update_settings,
    upsert_intel_item,
)
from .fetcher import cluster_items, fetch_all_feeds
from .notifier import dispatch_alert
from .publisher import (
    check_remote_heartbeat_age_minutes,
    publish_to_github_pages,
    write_local_state_json,
)

_SCAN_LOCK = threading.Lock()
_SCAN_STATE: Dict[str, Any] = {
    "scanning": False,
    "phase": "idle",
    "current": 0,
    "total": 0,
    "current_title": "",
    "last_error": "",
}


def run_scan_pipeline(max_clusters: int = MAX_LLM_EVAL_PER_SCAN) -> Dict[str, Any]:
    if not _SCAN_LOCK.acquire(blocking=False):
        return {"started": False, "reason": "already_scanning"}

    scan_id = record_scan_start()
    stats = {
        "fetched_count": 0,
        "new_clusters": 0,
        "major_alerts": 0,
        "archived_major": 0,
        "rumors_added": 0,
        "noise_filtered": 0,
    }
    _SCAN_STATE.update(
        {
            "scanning": True,
            "phase": "fetching",
            "current": 0,
            "total": 0,
            "current_title": "正在併發抓取全球 18 個新聞與爆料信源...",
            "last_error": "",
        }
    )

    try:
        settings = get_settings()
        model_id = settings.get("mlx_model")
        major_notify_min = int(settings.get("major_notify_min", 9))

        all_items = fetch_all_feeds(DEFAULT_FEEDS)
        stats["fetched_count"] = len(all_items)

        unseen_hashes = filter_unseen_urls([it["url_hash"] for it in all_items])
        fresh_items = [it for it in all_items if it["url_hash"] in unseen_hashes]

        if not fresh_items:
            record_scan_finish(scan_id, stats, status="ok", message="無新文章（全部已判讀過）")
            return {"started": True, "stats": stats}

        clusters = cluster_items(fresh_items)
        # Balance evaluation budget between global major streams and leak/rumor streams
        major_cls = [c for c in clusters if c.get("stream") != "rumor"]
        rumor_cls = [c for c in clusters if c.get("stream") == "rumor"]
        half = max(1, max_clusters // 2)
        eval_slice = major_cls[:half] + rumor_cls[:half]
        if len(eval_slice) < max_clusters:
            used_fps = {c["fingerprint"] for c in eval_slice}
            overflow = [c for c in clusters if c["fingerprint"] not in used_fps]
            eval_slice.extend(overflow[: max_clusters - len(eval_slice)])
        stats["new_clusters"] = len(eval_slice)

        _SCAN_STATE.update(
            {
                "phase": "loading_model",
                "total": len(eval_slice),
                "current": 0,
                "current_title": f"載入本地 MLX 模型 ({model_id})...",
            }
        )
        ensure_model_loaded(model_id)

        # Collect URLs belonging to evaluated clusters so un-evaluated overflow waits for next scan
        evaluated_urls = []
        for idx, cl in enumerate(eval_slice, start=1):
            _SCAN_STATE.update(
                {
                    "phase": "evaluating",
                    "current": idx,
                    "total": len(eval_slice),
                    "current_title": cl["lead_title"][:90],
                }
            )
            evaluated = evaluate_cluster(cl, model_id=model_id, is_sandbox=False)
            stored = upsert_intel_item(evaluated)

            kind = stored["kind"]
            if kind == "major":
                if stored["impact_level"] >= major_notify_min:
                    stats["major_alerts"] += 1
                else:
                    stats["archived_major"] += 1
            elif kind == "rumor":
                stats["rumors_added"] += 1
            else:
                stats["noise_filtered"] += 1

            if stored.get("should_notify"):
                dispatch_alert(stored, settings)

            for src in cl.get("sources", []):
                evaluated_urls.append(
                    {
                        "url_hash": hashlib.sha1(src["url"].strip().split("#")[0].encode("utf-8")).hexdigest()[:20],
                        "url": src["url"],
                        "title": src.get("title", ""),
                        "source_id": src.get("name", ""),
                    }
                )

        mark_urls_seen(evaluated_urls)
        msg = (
            f"審閱 {stats['new_clusters']} 組新事件："
            f"核彈警報 {stats['major_alerts']} 則、"
            f"靜音收錄 {stats['archived_major']} 則、"
            f"傳言 {stats['rumors_added']} 則、"
            f"攔截雜訊 {stats['noise_filtered']} 則"
        )
        # Always write sanitized local state.json and sync to GitHub Pages if configured
        gh_repo = settings.get("github_repo", "").strip()
        gh_token = settings.get("github_token", "").strip()
        node_name = settings.get("node_name", "Mac-Primary")
        if gh_repo and gh_token:
            pub_res = publish_to_github_pages(gh_repo, gh_token, node_name=node_name, model_id=model_id or "")
            if pub_res.get("ok"):
                msg += "（已同步至 GitHub Pages）"
        else:
            write_local_state_json(node_name=node_name, model_id=model_id or "")

        record_scan_finish(scan_id, stats, status="ok", message=msg)
        return {"started": True, "stats": stats}
    except Exception as exc:
        err_msg = str(exc)
        _SCAN_STATE["last_error"] = err_msg
        record_scan_finish(scan_id, stats, status="error", message=err_msg[:200])
        return {"started": False, "error": err_msg}
    finally:
        _SCAN_STATE.update(
            {
                "scanning": False,
                "phase": "idle",
                "current_title": "",
            }
        )
        _SCAN_LOCK.release()


def start_scan_async() -> bool:
    if _SCAN_STATE["scanning"]:
        return False
    t = threading.Thread(target=run_scan_pipeline, daemon=True)
    t.start()
    return True


def scheduler_loop() -> None:
    # Run initial scan automatically on boot if DB has 0 items
    time.sleep(1.5)
    board = query_board_state()
    if board["stats"]["total_evaluated"] == 0 and not _SCAN_STATE["scanning"]:
        start_scan_async()

    last_run = time.monotonic()
    while True:
        time.sleep(15)
        settings = get_settings()
        try:
            interval_min = max(5, int(settings.get("scan_interval_min", 15)))
        except ValueError:
            interval_min = 15
        if (time.monotonic() - last_run) >= interval_min * 60:
            if not _SCAN_STATE["scanning"]:
                # Secondary failover check: only take over if Primary heartbeat > 25 min stale
                if settings.get("node_role") == "secondary":
                    age = check_remote_heartbeat_age_minutes(
                        settings.get("github_repo", ""), settings.get("github_token", "")
                    )
                    if age is not None and age < 25.0:
                        last_run = time.monotonic()
                        continue
                start_scan_async()
            last_run = time.monotonic()


class IntelRadarHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        # Keep console output tidy
        pass

    def _send_json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/state":
            data = query_board_state()
            data["scan_state"] = dict(_SCAN_STATE)
            data["model_status"] = get_model_status()
            data["feeds"] = DEFAULT_FEEDS
            data["server_time"] = now_iso()
            self._send_json(data)
            return

        # Serve static files from WEB_DIR
        if path in ("/", "/index.html"):
            target = WEB_DIR / "index.html"
        else:
            rel = path.lstrip("/")
            target = (WEB_DIR / rel).resolve()
            if not str(target).startswith(str(WEB_DIR.resolve())):
                self.send_error(HTTPStatus.FORBIDDEN)
                return

        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        ctype, _ = mimetypes.guess_type(str(target))
        content = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{ctype or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/scan":
            started = start_scan_async()
            self._send_json({"ok": True, "started": started, "scan_state": dict(_SCAN_STATE)})
            return

        if path == "/api/settings":
            body = self._read_json_body()
            updated = update_settings(body)
            self._send_json({"ok": True, "settings": updated})
            return

        if path == "/api/publish":
            body = self._read_json_body()
            if body:
                update_settings(body)
            settings = get_settings()
            res = publish_to_github_pages(
                settings.get("github_repo", ""),
                settings.get("github_token", ""),
                node_name=settings.get("node_name", "Mac-Primary"),
                model_id=settings.get("mlx_model", ""),
            )
            if res.get("ok") and res.get("repo"):
                update_settings({"github_repo": res["repo"]})
            self._send_json(res, status=200 if res.get("ok") else 400)
            return

        if path == "/api/test-notify":
            settings = get_settings()
            sample = {
                "kind": "major",
                "domain": "系統測試",
                "impact_level": 10,
                "title_zh": "情報雷達推播連線測試成功",
                "summary_zh": "當全球發生 Lv.9–10 核彈級大事或傳言升檔為極高可信時，系統將立即透過此管道通知你。",
                "sources": [{"name": "Intel Radar", "url": "http://127.0.0.1:8765"}],
            }
            channels = dispatch_alert(sample, settings)
            self._send_json({"ok": True, "channels": channels})
            return

        if path == "/api/sandbox":
            body = self._read_json_body()
            title = (body.get("title") or "").strip()
            summary = (body.get("summary") or "").strip()
            source_type = body.get("source_type") or "tier1_multi"
            save_to_board = bool(body.get("save_to_board", True))
            fire_notify = bool(body.get("fire_notify", True))

            if not title:
                self._send_json({"ok": False, "error": "請輸入測試標題"}, status=400)
                return

            presets = {
                "tier1_multi": {
                    "sources": [
                        {"name": "Reuters", "url": "#sandbox", "title": title, "authority": "tier1"},
                        {"name": "Bloomberg", "url": "#sandbox2", "title": title, "authority": "tier1"},
                        {"name": "CNBC", "url": "#sandbox3", "title": title, "authority": "tier1"},
                    ],
                    "source_count": 3,
                    "tier1_count": 3,
                    "stream": "major",
                },
                "known_leaker": {
                    "sources": [
                        {"name": "MacRumors / Supply Chain", "url": "#sandbox", "title": title, "authority": "insider_media"}
                    ],
                    "source_count": 1,
                    "tier1_count": 0,
                    "stream": "rumor",
                },
                "hard_leak": {
                    "sources": [
                        {"name": "FCC / Benchmark Leak", "url": "#sandbox", "title": title, "authority": "insider_media"},
                        {"name": "9to5Mac", "url": "#sandbox2", "title": title, "authority": "insider_media"},
                    ],
                    "source_count": 2,
                    "tier1_count": 0,
                    "stream": "rumor",
                },
                "anon_blog": {
                    "sources": [
                        {"name": "Anonymous Forum", "url": "#sandbox", "title": title, "authority": "leak_aggregator"}
                    ],
                    "source_count": 1,
                    "tier1_count": 0,
                    "stream": "rumor",
                },
            }
            preset = presets.get(source_type, presets["tier1_multi"])
            fp = "sandbox_" + hashlib.sha1(f"{title}:{source_type}".encode("utf-8")).hexdigest()[:14]

            cluster = {
                "fingerprint": fp,
                "lead_title": title,
                "lead_summary": summary,
                "stream": preset["stream"],
                "domain": "AI・科技巨頭",
                "sources": preset["sources"],
                "source_count": preset["source_count"],
                "tier1_count": preset["tier1_count"],
                "hn_points": 350 if source_type == "tier1_multi" else 0,
            }

            settings = get_settings()
            t0 = time.perf_counter()
            evaluated = evaluate_cluster(cluster, model_id=settings.get("mlx_model"), is_sandbox=True)
            latency_ms = round((time.perf_counter() - t0) * 1000)

            notified_channels = []
            if save_to_board and evaluated["kind"] != "noise":
                stored = upsert_intel_item(evaluated)
                if fire_notify and stored.get("should_notify"):
                    notified_channels = dispatch_alert(stored, settings)
                evaluated = stored

            self._send_json(
                {
                    "ok": True,
                    "latency_ms": latency_ms,
                    "result": evaluated,
                    "notified_channels": notified_channels,
                }
            )
            return

        if path == "/api/sandbox/clear":
            removed = clear_sandbox_items()
            self._send_json({"ok": True, "removed": removed})
            return

        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    parser = argparse.ArgumentParser(description="Intel Radar Local Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    init_db()
    threading.Thread(target=scheduler_loop, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), IntelRadarHandler)
    print("=" * 62)
    print("📡 INTEL RADAR // 個人情報雷達 v0.1 已啟動")
    print(f"🌐 戰情室網址: http://{args.host}:{args.port}")
    print("🧠 本地守門員: Apple Silicon MLX (零雲端 API 費用)")
    print("💡 按 Ctrl + C 可停止服務")
    print("=" * 62)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 情報雷達已停止。")
        server.server_close()


if __name__ == "__main__":
    main()
