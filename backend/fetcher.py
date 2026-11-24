"""Concurrent RSS / Atom / Hacker News fetcher and multi-source story clusterer."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Set

from .config import DEFAULT_FEEDS, MAX_ITEMS_PER_FEED

USER_AGENT = "IntelRadar/0.1 (macOS; Personal Intelligence Aggregator)"

_STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "up", "about", "into", "over", "after", "is", "are",
    "was", "were", "be", "been", "has", "have", "had", "will", "would", "could",
    "should", "may", "might", "its", "it", "this", "that", "these", "those",
    "as", "new", "says", "said", "report", "reports", "how", "why", "what",
    "who", "when", "where", "more", "than", "now", "out", "just", "first",
    "all", "can", "get", "gets", "set", "sets", "year", "years", "day", "days",
}

_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.I)
_WORD_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\.\-\+\u4e00-\u9fff]{1,}")


def clean_html_text(raw: str, max_len: int = 460) -> str:
    if not raw:
        return ""
    txt = _TAG_RE.sub(" ", raw)
    txt = html.unescape(txt)
    txt = " ".join(txt.split())
    return txt[:max_len]


def count_external_domains(raw_html: str) -> int:
    """Count distinct news domains cited inside aggregators like TechMeme."""
    if not raw_html:
        return 0
    domains = set()
    for url in _HREF_RE.findall(raw_html):
        try:
            host = urllib.parse.urlparse(url).netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            if host and "techmeme.com" not in host:
                domains.add(host)
        except Exception:
            continue
    return len(domains)


def url_hash(url: str) -> str:
    normalized = url.strip().split("#")[0]
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:20]


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _child_text(elem: ET.Element, names: Set[str]) -> str:
    for child in elem:
        if _local_tag(child.tag).lower() in names:
            if child.text and child.text.strip():
                return child.text.strip()
    return ""


def _atom_link(elem: ET.Element) -> str:
    fallback = ""
    for child in elem:
        if _local_tag(child.tag).lower() == "link":
            href = (child.attrib.get("href") or "").strip()
            rel = (child.attrib.get("rel") or "alternate").lower()
            if href and rel == "alternate":
                return href
            if href and not fallback:
                fallback = href
            if child.text and child.text.strip() and not fallback:
                fallback = child.text.strip()
    return fallback


def fetch_single_feed(feed: Dict[str, str]) -> List[Dict[str, Any]]:
    req = urllib.request.Request(feed["url"], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw_bytes = resp.read()
    except Exception:
        return []

    if feed.get("kind") == "hn_json":
        try:
            payload = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
        except Exception:
            return []
        items: List[Dict[str, Any]] = []
        for hit in payload.get("hits", [])[:MAX_ITEMS_PER_FEED]:
            title = (hit.get("title") or "").strip()
            url = (hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}").strip()
            points = int(hit.get("points") or 0)
            comments = int(hit.get("num_comments") or 0)
            if not title or points < 80:
                continue
            items.append(
                {
                    "url": url,
                    "url_hash": url_hash(url),
                    "title": title,
                    "summary": f"Hacker News 熱度：{points} points / {comments} 則討論。",
                    "source_id": feed["id"],
                    "source_name": feed["name"],
                    "stream": feed["stream"],
                    "domain": feed["domain"],
                    "authority": feed["authority"],
                    "hn_points": points,
                    "cited_domains": 1,
                }
            )
        return items

    # Parse RSS 2.0 or Atom XML
    try:
        text = raw_bytes.decode("utf-8", errors="ignore").lstrip()
        root = ET.fromstring(text)
    except Exception:
        return []

    entries: List[ET.Element] = []
    for elem in root.iter():
        lname = _local_tag(elem.tag).lower()
        if lname in ("item", "entry"):
            entries.append(elem)

    out: List[Dict[str, Any]] = []
    for entry in entries[:MAX_ITEMS_PER_FEED]:
        title = clean_html_text(_child_text(entry, {"title"}), max_len=240)
        link = _child_text(entry, {"link"}) or _atom_link(entry)
        raw_desc = _child_text(entry, {"description", "summary", "content", "encoded"})
        summary = clean_html_text(raw_desc, max_len=460)
        if not title or not link:
            continue
        cited = max(1, count_external_domains(raw_desc)) if feed["id"] == "techmeme" else 1
        out.append(
            {
                "url": link.strip(),
                "url_hash": url_hash(link),
                "title": title,
                "summary": summary,
                "source_id": feed["id"],
                "source_name": feed["name"],
                "stream": feed["stream"],
                "domain": feed["domain"],
                "authority": feed["authority"],
                "hn_points": 0,
                "cited_domains": cited,
            }
        )
    return out


def fetch_all_feeds(feeds: List[Dict[str, str]] = DEFAULT_FEEDS) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(fetch_single_feed, f): f for f in feeds}
        for fut in as_completed(futs):
            try:
                results.extend(fut.result())
            except Exception:
                continue
    # Deduplicate exact URL hashes within batch
    seen = set()
    unique = []
    for it in results:
        if it["url_hash"] not in seen:
            seen.add(it["url_hash"])
            unique.append(it)
    return unique


def extract_key_tokens(text: str) -> Set[str]:
    tokens = set()
    for w in _WORD_RE.findall(text):
        lw = w.lower().strip(".-+")
        if len(lw) >= 3 and lw not in _STOPWORDS:
            tokens.add(lw)
    return tokens


def compute_fingerprint(tokens: Set[str], fallback_title: str) -> str:
    if len(tokens) >= 3:
        key_str = " ".join(sorted(tokens)[:6])
    else:
        key_str = fallback_title.lower().strip()
    return hashlib.sha1(key_str.encode("utf-8")).hexdigest()[:18]


def cluster_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group news items reporting the same underlying event across outlets."""
    clusters: List[Dict[str, Any]] = []

    for it in items:
        toks = extract_key_tokens(it["title"])
        matched = None
        best_sim = 0.0

        for cl in clusters:
            union = toks | cl["tokens"]
            if not union:
                continue
            inter = toks & cl["tokens"]
            jacc = len(inter) / len(union)
            if jacc >= 0.34 or (len(inter) >= 3 and jacc >= 0.24):
                if jacc > best_sim:
                    best_sim = jacc
                    matched = cl

        source_entry = {
            "name": it["source_name"],
            "url": it["url"],
            "title": it["title"],
            "authority": it["authority"],
        }

        if matched is not None:
            existing_names = {s["name"] for s in matched["sources"]}
            if it["source_name"] not in existing_names:
                matched["sources"].append(source_entry)
            matched["tokens"] |= toks
            matched["cited_domains"] = max(matched["cited_domains"], it.get("cited_domains", 1))
            matched["hn_points"] = max(matched["hn_points"], it.get("hn_points", 0))
            if it["authority"] == "tier1":
                matched["tier1_count"] += 1
            if len(it.get("summary", "")) > len(matched["lead_summary"]):
                matched["lead_summary"] = it["summary"]
        else:
            clusters.append(
                {
                    "lead_title": it["title"],
                    "lead_summary": it.get("summary", ""),
                    "stream": it["stream"],
                    "domain": it["domain"],
                    "tokens": set(toks),
                    "sources": [source_entry],
                    "tier1_count": 1 if it["authority"] == "tier1" else 0,
                    "cited_domains": it.get("cited_domains", 1),
                    "hn_points": it.get("hn_points", 0),
                }
            )

    # Finalize fingerprints and sort most corroborated clusters first
    for cl in clusters:
        cl["source_count"] = max(len(cl["sources"]), cl["cited_domains"])
        cl["fingerprint"] = compute_fingerprint(cl["tokens"], cl["lead_title"])
        cl.pop("tokens", None)

    clusters.sort(
        key=lambda c: (c["source_count"], c["tier1_count"], c["hn_points"]),
        reverse=True,
    )
    return clusters
