"""Render the Digest into a subject line + HTML email body."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .compose import Digest


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
    )
    return html


def _supports_dash() -> bool:
    """%-d works on Unix, not Windows. Detect once."""
    try:
        datetime(2020, 1, 5).strftime("%-d")
        return True
    except ValueError:
        return False
