"""Sanitized static state exporter & zero-bloat GitHub Pages publisher (with Primary/Secondary heartbeat)."""

import base64
from datetime import datetime, timezone
import json
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .config import WEB_DIR
from .db import now_iso, query_board_state

GITHUB_API = "https://api.github.com"


def build_public_state(node_name: str = "", model_id: str = "") -> Dict[str, Any]:
    """Build a strictly sanitized JSON payload safe for public GitHub Pages hosting."""
    raw = query_board_state()
    raw_settings = raw.get("settings") or {}
    host_label = node_name.strip() or socket.gethostname().split(".")[0] or "Mac-Primary"

    # Strip sandbox items and ALL secret tokens/webhooks!
    def _clean_list(items):
        return [it for it in (items or []) if not it.get("is_sandbox")]

    rumors_raw = raw.get("rumors") or {}
    public_payload = {
        "major_alerts": _clean_list(raw.get("major_alerts")),
        "major_archived": _clean_list(raw.get("major_archived")),
        "rumors": {
            "likely": _clean_list(rumors_raw.get("likely")),
            "watch": _clean_list(rumors_raw.get("watch")),
            "unlikely": _clean_list(rumors_raw.get("unlikely")),
        },
        "stats": raw.get("stats") or {},
        "last_scan": raw.get("last_scan"),
        "settings": {
            "major_notify_min": raw_settings.get("major_notify_min", "9"),
            "rumor_low_max": raw_settings.get("rumor_low_max", "33"),
            "rumor_watch_max": raw_settings.get("rumor_watch_max", "67"),
        },
        "heartbeat": {
            "updated_at": now_iso(),
            "node_name": host_label,
            "model_id": (model_id or raw_settings.get("mlx_model", "MLX")).split("/")[-1],
        },
        "static_mode": True,
    }
    return public_payload


def write_local_state_json(node_name: str = "", model_id: str = "") -> Dict[str, Any]:
    payload = build_public_state(node_name=node_name, model_id=model_id)
    out_path = WEB_DIR / "state.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _gh_request(method: str, path: str, token: str, body: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
    url = f"{GITHUB_API}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token.strip()}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "IntelRadar-Publisher/0.1",
    }
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"message": raw or str(err)}
        return err.code, parsed


def check_remote_heartbeat_age_minutes(repo: str, token: str) -> Optional[float]:
    """Return age in minutes of remote gh-pages state.json heartbeat, or None if not found."""
    repo = repo.strip().strip("/")
    if not repo or not token.strip():
        return None
    status, data = _gh_request("GET", f"/repos/{repo}/contents/state.json?ref=gh-pages", token)
    if status != 200 or not isinstance(data, dict) or "content" not in data:
        return None
    try:
        decoded = base64.b64decode(data["content"]).decode("utf-8")
        state = json.loads(decoded)
        ts_str = (state.get("heartbeat") or {}).get("updated_at")
        if not ts_str:
            return None
        dt = datetime.fromisoformat(ts_str)
        age_sec = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
        return max(0.0, age_sec / 60.0)
    except Exception:
        return None


def publish_to_github_pages(repo: str, token: str, node_name: str = "", model_id: str = "") -> Dict[str, Any]:
    """Publish web/ static UI + sanitized state.json as a single orphan commit on `gh-pages`.

    Using a parentless commit on `gh-pages` keeps repo size tiny forever (zero commit history bloat)
    and requires no git SSH setup on any machine.
    """
    repo = repo.strip().strip("/")
    token = token.strip()
    if not token:
        return {"ok": False, "error": "請先貼上 GitHub 授權碼 (Token)"}

    # Auto-detect GitHub username via GET /user if repo is omitted or has no owner prefix!
    if "/" not in repo:
        st_user, user_info = _gh_request("GET", "/user", token)
        if st_user != 200 or not isinstance(user_info, dict) or not user_info.get("login"):
            return {"ok": False, "error": f"授權碼驗證失敗 ({st_user})，請確認貼上的 ghp_... 是否完整"}
        owner = user_info["login"]
        repo_name = repo if repo else "intel-radar"
        repo = f"{owner}/{repo_name}"
    else:
        owner, repo_name = repo.split("/", 1)

    # Verify repo exists (or auto-create public repo if 404 and token has permission)
    st_repo, repo_info = _gh_request("GET", f"/repos/{repo}", token)
    if st_repo == 404:
        st_create, create_info = _gh_request(
            "POST",
            "/user/repos",
            token,
            {
                "name": repo_name,
                "description": "Intel Radar — Personal AI News & Rumor Mill Dashboard",
                "private": False,
                "auto_init": True,
            },
        )
        if st_create not in (200, 201):
            msg = create_info.get("message", "找不到此倉庫且無法自動建立")
            return {"ok": False, "error": f"找不到倉庫 {repo}（{msg}）。請先在 GitHub 建立空倉庫，並確認已提交至少一個初始檔案（勾選 Add a README）。"}

    state_payload = write_local_state_json(node_name=node_name, model_id=model_id)

    files_to_publish = {
        "index.html": (WEB_DIR / "index.html").read_text(encoding="utf-8"),
        "index.css": (WEB_DIR / "index.css").read_text(encoding="utf-8"),
        "app.js": (WEB_DIR / "app.js").read_text(encoding="utf-8"),
        "state.json": json.dumps(state_payload, ensure_ascii=False, indent=2),
        ".nojekyll": "",
    }

    tree_entries = []
    for path_name, text_content in files_to_publish.items():
        st_blob, blob_res = _gh_request(
            "POST",
            f"/repos/{repo}/git/blobs",
            token,
            {"content": text_content, "encoding": "utf-8"},
        )
        if st_blob not in (200, 201):
            err_detail = blob_res.get("message", str(blob_res))
            if "Git Repository is empty" in err_detail:
                # Seed an initial README on main so Git Data API unlocks
                _gh_request(
                    "PUT",
                    f"/repos/{repo}/contents/README.md",
                    token,
                    {
                        "message": "Initial commit",
                        "content": base64.b64encode(b"# Intel Radar\n").decode("ascii"),
                    },
                )
                st_blob, blob_res = _gh_request(
                    "POST",
                    f"/repos/{repo}/git/blobs",
                    token,
                    {"content": text_content, "encoding": "utf-8"},
                )
            if st_blob not in (200, 201):
                return {"ok": False, "error": f"上傳檔案失敗 ({st_blob}): {blob_res.get('message', '')}"}

        tree_entries.append(
            {
                "path": path_name,
                "mode": "100644",
                "type": "blob",
                "sha": blob_res["sha"],
            }
        )

    st_tree, tree_res = _gh_request(
        "POST",
        f"/repos/{repo}/git/trees",
        token,
        {"tree": tree_entries},
    )
    if st_tree not in (200, 201):
        return {"ok": False, "error": f"建立目錄樹失敗 ({st_tree}): {tree_res.get('message', '')}"}

    # Orphan commit (`parents: []`) -> zero commit history accumulation!
    commit_msg = f"Update Intel Radar state ({state_payload['heartbeat']['node_name']} @ {state_payload['heartbeat']['updated_at']})"
    st_commit, commit_res = _gh_request(
        "POST",
        f"/repos/{repo}/git/commits",
        token,
        {"message": commit_msg, "tree": tree_res["sha"], "parents": []},
    )
    if st_commit not in (200, 201):
        return {"ok": False, "error": f"建立 Commit 失敗 ({st_commit}): {commit_res.get('message', '')}"}

    commit_sha = commit_res["sha"]

    # Update or create refs/heads/gh-pages
    st_ref, _ = _gh_request(
        "PATCH",
        f"/repos/{repo}/git/refs/heads/gh-pages",
        token,
        {"sha": commit_sha, "force": True},
    )
    if st_ref not in (200, 201):
        st_create_ref, ref_err = _gh_request(
            "POST",
            f"/repos/{repo}/git/refs",
            token,
            {"ref": "refs/heads/gh-pages", "sha": commit_sha},
        )
        if st_create_ref not in (200, 201):
            return {"ok": False, "error": f"更新 gh-pages 分支失敗 ({st_create_ref}): {ref_err.get('message', '')}"}

    # Try enabling GitHub Pages on gh-pages branch if not yet enabled (409 = already enabled)
    _gh_request(
        "POST",
        f"/repos/{repo}/pages",
        token,
        {"source": {"branch": "gh-pages", "path": "/"}},
    )

    pages_url = f"https://{owner}.github.io/{repo_name}/"
    return {
        "ok": True,
        "repo": repo,
        "pages_url": pages_url,
        "updated_at": state_payload["heartbeat"]["updated_at"],
        "node_name": state_payload["heartbeat"]["node_name"],
    }
