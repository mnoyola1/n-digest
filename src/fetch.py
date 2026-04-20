"""Fetch items from all sources defined in sources.yml.

Each source type (rss, atom, hn_search, github_search, hf_papers) has its own
adapter. All adapters produce a normalized list of `Item` objects with a stable
SHA-256 id derived from the canonicalized URL so dedup is cross-source.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import feedparser
import httpx
import yaml
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

DEFAULT_MAX_AGE_HOURS = 72
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
USER_AGENT = (
    "Mozilla/5.0 (compatible; n-digest/1.0; +https://github.com/mnoyola1/n-digest)"
)

# Tracking params we strip for canonicalization.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "mc_cid", "mc_eid", "fbclid", "gclid", "ref", "ref_src", "ref_url",
    "s", "sh", "feature", "ncid",
}


@dataclass
class Item:
    """A normalized feed item."""
    id: str
    title: str
    url: str
    source: str
    tags: list[str]
    published_at: str  # ISO8601 UTC
    summary: str = ""
    raw_text: str = ""
    score: float | None = None  # filled in by filter stage
    priority_tag: str | None = None
    cluster_id: str | None = None
    rationale: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonicalize_url(url: str) -> str:
    """Lowercase host, strip tracking query params, drop fragment, normalize trailing slash."""
    try:
        parts = urlsplit(url.strip())
        netloc = parts.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        query_pairs = [
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if k.lower() not in _TRACKING_PARAMS
        ]
        query = urlencode(query_pairs)
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower() or "https", netloc, path, query, ""))
    except Exception:
        return url.strip()


def make_id(url: str) -> str:
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()[:16]


def clean_text(html_or_text: str, max_chars: int = 1200) -> str:
    """Strip HTML and collapse whitespace."""
    if not html_or_text:
        return ""
    soup = BeautifulSoup(html_or_text, "html.parser")
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "..."
    return text


def _parse_dt(value: Any) -> datetime | None:
    """Parse various date formats to a tz-aware UTC datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        from dateutil import parser as dateparser
        try:
            dt = dateparser.parse(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            return None
    # feedparser time.struct_time
    try:
        import time
        return datetime.fromtimestamp(time.mktime(value), tz=timezone.utc)  # type: ignore[arg-type]
    except Exception:
        return None


def _passes_keyword_filter(item: Item, keywords: list[str] | None) -> bool:
    if not keywords:
        return True
    haystack = (item.title + " " + item.summary).lower()
    return any(kw.lower() in haystack for kw in keywords)


def _passes_age(item: Item, max_age_hours: int) -> bool:
    dt = _parse_dt(item.published_at)
    if dt is None:
        return True  # keep undated items, let the LLM sort it out
    return dt >= datetime.now(timezone.utc) - timedelta(hours=max_age_hours)


def _http_client() -> httpx.Client:
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    return httpx.Client(timeout=HTTP_TIMEOUT, headers=headers, follow_redirects=True)


# -------- adapters --------

def fetch_rss(source: dict) -> list[Item]:
    url = source["url"]
    name = source["name"]
    tags = source.get("tags", [])
    with _http_client() as client:
        resp = client.get(url)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)

    items: list[Item] = []
    for entry in parsed.entries[:40]:
        link = entry.get("link") or entry.get("id") or ""
        if not link:
            continue
        title = clean_text(entry.get("title", ""), 300)
        summary = clean_text(entry.get("summary", "") or entry.get("description", ""), 800)
        content_list = entry.get("content") or []
        raw = ""
        if content_list:
            raw = clean_text(content_list[0].get("value", ""), 3000)
        published = _parse_dt(
            entry.get("published_parsed")
            or entry.get("updated_parsed")
            or entry.get("published")
            or entry.get("updated")
        )
        items.append(Item(
            id=make_id(link),
            title=title or "(untitled)",
            url=link,
            source=name,
            tags=list(tags),
            published_at=(published or datetime.now(timezone.utc)).isoformat(),
            summary=summary,
            raw_text=raw or summary,
        ))
    return items


def fetch_hn_search(source: dict) -> list[Item]:
    url = source["url"]
    name = source["name"]
    tags = source.get("tags", [])
    with _http_client() as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()

    items: list[Item] = []
    for hit in data.get("hits", [])[:30]:
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        title = hit.get("title") or hit.get("story_title") or ""
        if not title:
            continue
        created = hit.get("created_at")
        points = hit.get("points") or 0
        comments = hit.get("num_comments") or 0
        summary = f"HN: {points} points, {comments} comments"
        items.append(Item(
            id=make_id(link),
            title=title,
            url=link,
            source=name,
            tags=list(tags),
            published_at=(_parse_dt(created) or datetime.now(timezone.utc)).isoformat(),
            summary=summary,
            raw_text=summary,
        ))
    return items


def fetch_github_search(source: dict) -> list[Item]:
    raw_url = source["url"]
    name = source["name"]
    tags = source.get("tags", [])
    pushed_since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    url = raw_url.replace("{{pushed_since}}", pushed_since)

    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with _http_client() as client:
        resp = client.get(url, headers=headers)
        if resp.status_code == 403:
            log.warning("GitHub API rate-limited for %s", name)
            return []
        resp.raise_for_status()
        data = resp.json()

    items: list[Item] = []
    for repo in data.get("items", [])[:15]:
        link = repo.get("html_url")
        if not link:
            continue
        title = f"{repo.get('full_name')} - {repo.get('description') or ''}"[:300]
        stars = repo.get("stargazers_count", 0)
        lang = repo.get("language", "")
        desc = repo.get("description") or ""
        summary = f"{stars:,} stars - {lang} - {desc}"[:600]
        items.append(Item(
            id=make_id(link),
            title=title,
            url=link,
            source=name,
            tags=list(tags),
            published_at=(_parse_dt(repo.get("pushed_at")) or datetime.now(timezone.utc)).isoformat(),
            summary=summary,
            raw_text=summary,
        ))
    return items


def fetch_hf_papers(source: dict) -> list[Item]:
    url = source["url"]
    name = source["name"]
    tags = source.get("tags", [])
    with _http_client() as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()

    items: list[Item] = []
    entries = data if isinstance(data, list) else data.get("papers", [])
    for entry in entries[:25]:
        paper = entry.get("paper") or entry
        arxiv_id = paper.get("id") or paper.get("arxiv_id")
        if not arxiv_id:
            continue
        link = f"https://huggingface.co/papers/{arxiv_id}"
        title = paper.get("title", "").strip()
        summary = clean_text(paper.get("summary", ""), 800)
        published = _parse_dt(paper.get("publishedAt") or entry.get("publishedAt"))
        upvotes = paper.get("upvotes") or entry.get("upvotes") or 0
        items.append(Item(
            id=make_id(link),
            title=title or f"arXiv:{arxiv_id}",
            url=link,
            source=name,
            tags=list(tags),
            published_at=(published or datetime.now(timezone.utc)).isoformat(),
            summary=f"[HF upvotes: {upvotes}] {summary}",
            raw_text=summary,
        ))
    return items


_ADAPTERS = {
    "rss": fetch_rss,
    "atom": fetch_rss,  # feedparser handles both
    "hn_search": fetch_hn_search,
    "github_search": fetch_github_search,
    "hf_papers": fetch_hf_papers,
}


# -------- entry point --------

def load_sources(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("sources", [])


def fetch_all(sources_path: str) -> tuple[list[Item], dict[str, Any]]:
    """Fetch every source; return (items, stats).

    Errors on individual sources are logged and do not abort the run.
    """
    sources = load_sources(sources_path)
    all_items: list[Item] = []
    per_source: dict[str, int] = {}
    failures: list[dict[str, str]] = []

    for source in sources:
        name = source["name"]
        kind = source.get("type", "rss")
        adapter = _ADAPTERS.get(kind)
        if adapter is None:
            log.warning("Unknown source type '%s' for %s", kind, name)
            continue
        try:
            raw_items = adapter(source)
        except Exception as exc:
            log.warning("Fetch failed for %s: %s", name, exc)
            failures.append({"source": name, "error": str(exc)[:200]})
            per_source[name] = 0
            continue

        max_age = int(source.get("max_age_hours", DEFAULT_MAX_AGE_HOURS))
        keywords = source.get("keyword_filter")
        filtered = [
            it for it in raw_items
            if _passes_age(it, max_age) and _passes_keyword_filter(it, keywords)
        ]
        per_source[name] = len(filtered)
        all_items.extend(filtered)
        log.info("Fetched %s: %d raw, %d after filters", name, len(raw_items), len(filtered))

    # Dedup by id within this run (same URL discovered via multiple feeds).
    seen: set[str] = set()
    unique: list[Item] = []
    for it in all_items:
        if it.id in seen:
            continue
        seen.add(it.id)
        unique.append(it)

    stats = {
        "total_raw": len(all_items),
        "total_unique": len(unique),
        "per_source": per_source,
        "failures": failures,
    }
    return unique, stats
