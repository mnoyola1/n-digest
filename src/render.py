"""Render the Digest into a subject line + HTML email body."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .compose import Digest

# Category color palette. Each entry has a strong accent color and a soft tint
# for card backgrounds. Values chosen to read well on both white and near-black
# email backgrounds (Gmail iOS dark mode).
CATEGORY_PALETTE: dict[str, dict[str, str]] = {
    "enterprise_utilities": {"label": "Enterprise AI \u00b7 Utilities", "color": "#2563eb", "tint": "#eff6ff"},
    "agentic":              {"label": "Agentic Systems",               "color": "#7c3aed", "tint": "#f5f3ff"},
    "oracle":               {"label": "Oracle Ecosystem",              "color": "#c2410c", "tint": "#fff7ed"},
    "foundation_models":    {"label": "Foundation Models",             "color": "#d97706", "tint": "#fffbeb"},
    "rpa_ai":               {"label": "RPA + AI",                      "color": "#0891b2", "tint": "#ecfeff"},
    "governance":           {"label": "AI Governance",                 "color": "#475569", "tint": "#f1f5f9"},
    "dev_tools":            {"label": "Developer Tools",               "color": "#059669", "tint": "#ecfdf5"},
    "rag_prompt":           {"label": "RAG \u0026 Prompting",          "color": "#db2777", "tint": "#fdf2f8"},
    "edtech":               {"label": "EdTech \u00b7 N Learn",         "color": "#4f46e5", "tint": "#eef2ff"},
    "wearable":             {"label": "Wearable AI",                   "color": "#0d9488", "tint": "#f0fdfa"},
    "other":                {"label": "AI Industry",                   "color": "#71717a", "tint": "#f4f4f5"},
}


def _env() -> Environment:
    templates_dir = Path(__file__).resolve().parent.parent / "templates"
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_subject(digest: Digest, now_et: datetime) -> str:
    date_bit = now_et.strftime("%b %d").replace(" 0", " ")
    words = digest.subject_headline.split()
    headline = " ".join(words[:6]) if len(words) > 6 else digest.subject_headline
    return f"AI Digest \u2022 {date_bit} \u2022 {headline}"


def render_html(digest: Digest, now_et: datetime, yesterday_url: str = "") -> str:
    subject_headline = digest.subject_headline
    env = _env()
    template = env.get_template("digest.html")
    subject = render_subject(digest, now_et)
    html = template.render(
        subject=subject,
        subject_headline=subject_headline,
        preheader=digest.top_story_preview or subject_headline,
        date_long=now_et.strftime("%A, %B %-d, %Y") if _supports_dash() else now_et.strftime("%A, %B %d, %Y").replace(" 0", " "),
        date_short=now_et.strftime("%b %d, %Y").replace(" 0", " "),
        what_matters=digest.what_matters_today,
        quick_hits=digest.quick_hits,
        deeper_look=digest.deeper_look,
        reviewed_count=digest.reviewed_count,
        yesterday_url=yesterday_url,
        categories=CATEGORY_PALETTE,
    )
    return html


def _supports_dash() -> bool:
    """%-d works on Unix, not Windows. Detect once."""
    try:
        datetime(2020, 1, 5).strftime("%-d")
        return True
    except ValueError:
        return False
