"""Notification dispatcher: macOS native notifications + ready-to-use mobile webhooks (ntfy / Bark / Telegram)."""

import json
import subprocess
import urllib.parse
import urllib.request
from typing import Any, Dict, List


def _escape_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def send_macos_notification(title: str, subtitle: str, message: str) -> bool:
    script = (
        f'display notification "{_escape_applescript(message)}" '
        f'with title "{_escape_applescript(title)}" '
        f'subtitle "{_escape_applescript(subtitle)}" '
        f'sound name "Glass"'
    )
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=4, capture_output=True)
        return True
    except Exception:
        return False


def send_ntfy(topic: str, title: str, message: str, url: str = "", priority: str = "urgent") -> bool:
    topic = topic.strip().strip("/")
    if not topic:
        return False
    endpoint = topic if topic.startswith("http") else f"https://ntfy.sh/{topic}"
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": priority,
        "Tags": "rotating_light,earth_asia",
    }
    if url:
        headers["Click"] = url
    req = urllib.request.Request(endpoint, data=message.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def send_bark(bark_base_url: str, title: str, message: str, url: str = "") -> bool:
    base = bark_base_url.strip().rstrip("/")
    if not base:
        return False
    payload = {
        "title": title,
        "body": message,
        "group": "IntelRadar",
        "level": "timeSensitive",
    }
    if url:
        payload["url"] = url
    req = urllib.request.Request(
        f"{base}/push",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def send_telegram(bot_token: str, chat_id: str, title: str, message: str, url: str = "") -> bool:
    bot_token = bot_token.strip()
    chat_id = chat_id.strip()
    if not bot_token or not chat_id:
        return False
    text = f"*{title}*\n{message}"
    if url:
        text += f"\n\n🔗 {url}"
    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "false"}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def dispatch_alert(item: Dict[str, Any], settings: Dict[str, str]) -> List[str]:
    """Send notifications across all enabled channels. Returns list of channels triggered."""
    channels: List[str] = []
    kind = item.get("kind")
    sources = item.get("sources") or []
    first_url = sources[0].get("url", "") if sources else ""

    if kind == "major":
        title = f"🚨 重大情報 Lv.{item.get('impact_level', 9)}｜{item.get('domain', '全球')}"
        subtitle = item.get("title_zh", "")
        body = f"{item.get('title_zh', '')} — {item.get('summary_zh', '')}"
    else:
        title = f"🕵️ 傳言升檔 ({item.get('credibility_pct', 75)}%)｜{item.get('domain', '情報')}"
        subtitle = item.get("title_zh", "")
        body = f"{item.get('title_zh', '')} — 已獲新證據升級為「可能性極高」"

    if settings.get("notify_macos", "true").lower() == "true":
        if send_macos_notification(title, subtitle, item.get("summary_zh", "")[:140]):
            channels.append("macOS")

    if settings.get("ntfy_topic", "").strip():
        if send_ntfy(settings["ntfy_topic"], title, body, first_url):
            channels.append("ntfy")

    if settings.get("bark_url", "").strip():
        if send_bark(settings["bark_url"], title, body, first_url):
            channels.append("Bark")

    if settings.get("telegram_bot_token", "").strip() and settings.get("telegram_chat_id", "").strip():
        if send_telegram(settings["telegram_bot_token"], settings["telegram_chat_id"], title, body, first_url):
            channels.append("Telegram")

    return channels
