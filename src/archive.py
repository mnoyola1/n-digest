"""Write each digest to docs/archive/ and regenerate docs/index.html.

GitHub Pages serves /docs as the site root, so the archive index will be at
https://<user>.github.io/n-digest/ and each day at /archive/YYYY-MM-DD.html.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
ARCHIVE_DIR = DOCS_DIR / "archive"
TEMPLATES_DIR = REPO_ROOT / "templates"


def archive_path_for(date_str: str) -> Path:
    return ARCHIVE_DIR / f"{date_str}.html"


def archive_url_for(date_str: str, base_url: str | None = None) -> str:
    base = base_url or "https://mnoyola1.github.io/n-digest/archive"
    return f"{base.rstrip('/')}/{date_str}.html"


def write_archive(date_str: str, html: str) -> Path:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = archive_path_for(date_str)
    path.write_text(html, encoding="utf-8")
    log.info("Archived digest to %s", path)
    return path


_SUBJECT_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_HEADLINE_RE = re.compile(r"<h1[^>]*class=\"title\"[^>]*>(.*?)</h1>", re.DOTALL | re.IGNORECASE)


def _extract_headline(html: str) -> str:
    m = _HEADLINE_RE.search(html)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = _SUBJECT_RE.search(html)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return "(untitled)"


def rebuild_index(limit: int = 30) -> Path:
    """Regenerate docs/index.html listing the most recent `limit` archives."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    entries = []
    for path in sorted(ARCHIVE_DIR.glob("*.html"), reverse=True)[:limit]:
        date_str = path.stem
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        try:
            html = path.read_text(encoding="utf-8")
        except OSError:
            continue
        headline = _extract_headline(html)
        entries.append({
            "date_str": date_str,
            "date_pretty": date_obj.strftime("%A, %B %d, %Y").replace(" 0", " "),
            "headline": headline,
            "href": f"archive/{date_str}.html",
        })

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("archive_index.html")
    html = template.render(entries=entries, generated_at=datetime.utcnow().isoformat() + "Z")
    index_path = DOCS_DIR / "index.html"
    index_path.write_text(html, encoding="utf-8")
    log.info("Rebuilt archive index with %d entries", len(entries))
    return index_path
