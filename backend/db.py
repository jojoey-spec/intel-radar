"""SQLite storage, deduplication, and rumor upgrade tracking for Intel Radar."""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import (
    DATA_DIR,
    DB_PATH,
    DEFAULT_MLX_MODEL,
    DEFAULT_SCAN_INTERVAL_MIN,
    MAJOR_ARCHIVE_MIN_LEVEL,
    MAJOR_NOTIFY_MIN_LEVEL,
    RUMOR_LOW_MAX,
    RUMOR_WATCH_MAX,
)

_DB_LOCK = threading.Lock()

DEFAULT_SETTINGS = {
    "mlx_model": DEFAULT_MLX_MODEL,
    "notify_macos": "true",
    "ntfy_topic": "",
    "bark_url": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "github_repo": "",
    "github_token": "",
    "node_role": "primary",
    "node_name": "Mac-Primary",
    "major_notify_min": str(MAJOR_NOTIFY_MIN_LEVEL),
    "major_archive_min": str(MAJOR_ARCHIVE_MIN_LEVEL),
    "rumor_low_max": str(RUMOR_LOW_MAX),
    "rumor_watch_max": str(RUMOR_WATCH_MAX),
    "scan_interval_min": str(DEFAULT_SCAN_INTERVAL_MIN),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _DB_LOCK:
        conn = get_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen_urls (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    title TEXT,
                    source_id TEXT,
                    fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS intel_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT UNIQUE NOT NULL,
                    kind TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    title_zh TEXT NOT NULL,
                    summary_zh TEXT NOT NULL,
                    raw_title TEXT NOT NULL,
                    impact_level INTEGER DEFAULT 0,
                    credibility_pct INTEGER DEFAULT 0,
                    rumor_tier TEXT DEFAULT '',
                    upgraded INTEGER DEFAULT 0,
                    evidence_tags_json TEXT DEFAULT '[]',
                    reasoning_zh TEXT DEFAULT '',
                    sources_json TEXT DEFAULT '[]',
                    source_count INTEGER DEFAULT 1,
                    notified INTEGER DEFAULT 0,
                    is_sandbox INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_intel_kind_updated
                    ON intel_items(kind, updated_at DESC);

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scan_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    fetched_count INTEGER DEFAULT 0,
                    new_clusters INTEGER DEFAULT 0,
                    major_alerts INTEGER DEFAULT 0,
                    archived_major INTEGER DEFAULT 0,
                    rumors_added INTEGER DEFAULT 0,
                    noise_filtered INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    message TEXT DEFAULT ''
                );
                """
            )
            for k, v in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (k, v),
                )
            conn.commit()
        finally:
            conn.close()


def get_settings() -> Dict[str, str]:
    with _DB_LOCK:
        conn = get_conn()
        try:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            out = dict(DEFAULT_SETTINGS)
            for r in rows:
                out[r["key"]] = r["value"]
            return out
        finally:
            conn.close()


def update_settings(updates: Dict[str, Any]) -> Dict[str, str]:
    with _DB_LOCK:
        conn = get_conn()
        try:
            for k, v in updates.items():
                if k in DEFAULT_SETTINGS:
                    conn.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (k, str(v).strip()),
                    )
            conn.commit()
        finally:
            conn.close()
    return get_settings()


def filter_unseen_urls(url_hashes: List[str]) -> set:
    if not url_hashes:
        return set()
    with _DB_LOCK:
        conn = get_conn()
        try:
            placeholders = ",".join("?" for _ in url_hashes)
            rows = conn.execute(
                f"SELECT url_hash FROM seen_urls WHERE url_hash IN ({placeholders})",
                url_hashes,
            ).fetchall()
            seen = {r["url_hash"] for r in rows}
            return set(url_hashes) - seen
        finally:
            conn.close()


def mark_urls_seen(items: List[Dict[str, str]]) -> None:
    if not items:
        return
    ts = now_iso()
    with _DB_LOCK:
        conn = get_conn()
        try:
            conn.executemany(
                "INSERT OR IGNORE INTO seen_urls (url_hash, url, title, source_id, fetched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (it["url_hash"], it["url"], it.get("title", ""), it.get("source_id", ""), ts)
                    for it in items
                ],
            )
            conn.commit()
        finally:
            conn.close()


def rumor_tier_for(pct: int, low_max: int = RUMOR_LOW_MAX, watch_max: int = RUMOR_WATCH_MAX) -> str:
    if pct <= low_max:
        return "unlikely"
    if pct <= watch_max:
        return "watch"
    return "likely"


_TIER_RANK = {"": 0, "unlikely": 1, "watch": 2, "likely": 3}


def upsert_intel_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Insert or merge an evaluated intelligence item.

    Returns dict with stored row + flags `was_upgraded` and `should_notify`.
    """
    settings = get_settings()
    major_notify_min = int(settings.get("major_notify_min", MAJOR_NOTIFY_MIN_LEVEL))
    low_max = int(settings.get("rumor_low_max", RUMOR_LOW_MAX))
    watch_max = int(settings.get("rumor_watch_max", RUMOR_WATCH_MAX))

    ts = now_iso()
    fp = item["fingerprint"]
    kind = item["kind"]
    pct = int(item.get("credibility_pct", 0))
    tier = rumor_tier_for(pct, low_max, watch_max) if kind == "rumor" else ""

    with _DB_LOCK:
        conn = get_conn()
        try:
            existing = conn.execute(
                "SELECT * FROM intel_items WHERE fingerprint = ?", (fp,)
            ).fetchone()

            was_upgraded = False
            should_notify = False

            if existing:
                old_sources = json.loads(existing["sources_json"] or "[]")
                seen_u = {s.get("url") for s in old_sources}
                merged_sources = list(old_sources)
                for s in item.get("sources", []):
                    if s.get("url") not in seen_u:
                        merged_sources.append(s)
                        seen_u.add(s.get("url"))

                new_source_count = max(int(existing["source_count"]), len(merged_sources), int(item.get("source_count", 1)))
                new_impact = max(int(existing["impact_level"]), int(item.get("impact_level", 0)))

                # Corroboration boost when new independent sources join an existing rumor
                added_sources = max(0, new_source_count - int(existing["source_count"]))
                new_pct = max(int(existing["credibility_pct"]), pct)
                if kind == "rumor" and added_sources > 0:
                    new_pct = min(96, new_pct + added_sources * 12)
                new_tier = rumor_tier_for(new_pct, low_max, watch_max) if kind == "rumor" else ""

                old_rank = _TIER_RANK.get(existing["rumor_tier"], 0)
                new_rank = _TIER_RANK.get(new_tier, 0)
                if kind == "rumor" and new_rank > old_rank and old_rank > 0:
                    was_upgraded = True

                already_notified = bool(existing["notified"])
                if kind == "major" and new_impact >= major_notify_min and not already_notified:
                    should_notify = True
                elif kind == "rumor" and was_upgraded and new_tier == "likely":
                    should_notify = True

                tags = list(dict.fromkeys(json.loads(existing["evidence_tags_json"] or "[]") + item.get("evidence_tags", [])))
                if was_upgraded and "⬆️ 可信度升檔" not in tags:
                    tags.insert(0, "⬆️ 可信度升檔")

                conn.execute(
                    """
                    UPDATE intel_items
                    SET title_zh = ?,
                        summary_zh = ?,
                        impact_level = ?,
                        credibility_pct = ?,
                        rumor_tier = ?,
                        upgraded = ?,
                        evidence_tags_json = ?,
                        reasoning_zh = ?,
                        sources_json = ?,
                        source_count = ?,
                        notified = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        item["title_zh"],
                        item["summary_zh"],
                        new_impact,
                        new_pct,
                        new_tier,
                        1 if (was_upgraded or existing["upgraded"]) else 0,
                        json.dumps(tags, ensure_ascii=False),
                        item.get("reasoning_zh", existing["reasoning_zh"]),
                        json.dumps(merged_sources, ensure_ascii=False),
                        new_source_count,
                        1 if (already_notified or should_notify) else 0,
                        ts,
                        existing["id"],
                    ),
                )
                row_id = existing["id"]
            else:
                impact = int(item.get("impact_level", 0))
                if kind == "major" and impact >= major_notify_min:
                    should_notify = True

                conn.execute(
                    """
                    INSERT INTO intel_items (
                        fingerprint, kind, domain, title_zh, summary_zh, raw_title,
                        impact_level, credibility_pct, rumor_tier, upgraded,
                        evidence_tags_json, reasoning_zh, sources_json, source_count,
                        notified, is_sandbox, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fp,
                        kind,
                        item.get("domain", "綜合情報"),
                        item["title_zh"],
                        item["summary_zh"],
                        item.get("raw_title", item["title_zh"]),
                        impact,
                        pct,
                        tier,
                        0,
                        json.dumps(item.get("evidence_tags", []), ensure_ascii=False),
                        item.get("reasoning_zh", ""),
                        json.dumps(item.get("sources", []), ensure_ascii=False),
                        int(item.get("source_count", max(1, len(item.get("sources", []))))),
                        1 if should_notify else 0,
                        1 if item.get("is_sandbox") else 0,
                        ts,
                        ts,
                    ),
                )
                row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            conn.commit()
            saved = conn.execute("SELECT * FROM intel_items WHERE id = ?", (row_id,)).fetchone()
            result = _row_to_dict(saved)
            result["was_upgraded"] = was_upgraded
            result["should_notify"] = should_notify
            return result
        finally:
            conn.close()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["evidence_tags"] = json.loads(d.pop("evidence_tags_json", "[]") or "[]")
    d["sources"] = json.loads(d.pop("sources_json", "[]") or "[]")
    d["upgraded"] = bool(d.get("upgraded"))
    d["notified"] = bool(d.get("notified"))
    d["is_sandbox"] = bool(d.get("is_sandbox"))
    return d


def query_board_state() -> Dict[str, Any]:
    settings = get_settings()
    major_notify_min = int(settings.get("major_notify_min", MAJOR_NOTIFY_MIN_LEVEL))
    major_archive_min = int(settings.get("major_archive_min", MAJOR_ARCHIVE_MIN_LEVEL))
    low_max = int(settings.get("rumor_low_max", RUMOR_LOW_MAX))
    watch_max = int(settings.get("rumor_watch_max", RUMOR_WATCH_MAX))

    with _DB_LOCK:
        conn = get_conn()
        try:
            major_rows = conn.execute(
                "SELECT * FROM intel_items WHERE kind = 'major' AND impact_level >= ? "
                "ORDER BY impact_level DESC, updated_at DESC LIMIT 80",
                (major_archive_min,),
            ).fetchall()

            rumor_rows = conn.execute(
                "SELECT * FROM intel_items WHERE kind = 'rumor' "
                "ORDER BY updated_at DESC LIMIT 120"
            ).fetchall()

            noise_count = conn.execute(
                "SELECT COUNT(*) FROM intel_items WHERE kind = 'noise' OR (kind = 'major' AND impact_level < ?)",
                (major_archive_min,),
            ).fetchone()[0]
            total_seen = conn.execute("SELECT COUNT(*) FROM seen_urls").fetchone()[0]
            last_scan = conn.execute(
                "SELECT * FROM scan_logs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()

    major_alerts = []
    major_archived = []
    for r in major_rows:
        item = _row_to_dict(r)
        if item["impact_level"] >= major_notify_min:
            major_alerts.append(item)
        else:
            major_archived.append(item)

    rumors = {"likely": [], "watch": [], "unlikely": []}
    for r in rumor_rows:
        item = _row_to_dict(r)
        tier = rumor_tier_for(item["credibility_pct"], low_max, watch_max)
        item["rumor_tier"] = tier
        rumors[tier].append(item)

    for k in rumors:
        rumors[k].sort(key=lambda x: (x["credibility_pct"], x["updated_at"]), reverse=True)

    total_evaluated = len(major_rows) + len(rumor_rows) + noise_count
    noise_ratio = round((noise_count / total_evaluated) * 100) if total_evaluated > 0 else 0

    return {
        "major_alerts": major_alerts,
        "major_archived": major_archived,
        "rumors": rumors,
        "stats": {
            "total_seen_urls": total_seen,
            "total_evaluated": total_evaluated,
            "noise_filtered": noise_count,
            "noise_ratio_pct": noise_ratio,
            "major_alert_count": len(major_alerts),
            "major_archive_count": len(major_archived),
            "rumor_count": len(rumor_rows),
        },
        "last_scan": dict(last_scan) if last_scan else None,
        "settings": settings,
    }


def clear_sandbox_items() -> int:
    with _DB_LOCK:
        conn = get_conn()
        try:
            cur = conn.execute("DELETE FROM intel_items WHERE is_sandbox = 1")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def record_scan_start() -> int:
    with _DB_LOCK:
        conn = get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO scan_logs (started_at, status, message) VALUES (?, 'running', '掃描進行中...')",
                (now_iso(),),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def record_scan_finish(scan_id: int, stats: Dict[str, Any], status: str = "ok", message: str = "") -> None:
    with _DB_LOCK:
        conn = get_conn()
        try:
            conn.execute(
                """
                UPDATE scan_logs
                SET finished_at = ?,
                    fetched_count = ?,
                    new_clusters = ?,
                    major_alerts = ?,
                    archived_major = ?,
                    rumors_added = ?,
                    noise_filtered = ?,
                    status = ?,
                    message = ?
                WHERE id = ?
                """,
                (
                    now_iso(),
                    stats.get("fetched_count", 0),
                    stats.get("new_clusters", 0),
                    stats.get("major_alerts", 0),
                    stats.get("archived_major", 0),
                    stats.get("rumors_added", 0),
                    stats.get("noise_filtered", 0),
                    status,
                    message,
                    scan_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
