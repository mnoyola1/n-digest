"""Second-stage curation: write the digest body with Claude Opus."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from .fetch import Item

log = logging.getLogger(__name__)

DEFAULT_COMPOSE_MODEL = "claude-opus-4-7"


@dataclass
class ComposedItem:
    item_id: str
    headline: str
    url: str
    source: str
    read_time_min: int
    body: str = ""  # for what_matters_today: why_it_matters; for deeper_look: pitch


@dataclass
class Digest:
    subject_headline: str
    top_story_preview: str
    what_matters_today: list[ComposedItem]
    quick_hits: list[ComposedItem]
    deeper_look: ComposedItem | None
    reviewed_count: int = 0


def _load_system_prompt() -> str:
    path = Path(__file__).resolve().parent.parent / "prompts" / "compose_system.md"
    return path.read_text(encoding="utf-8")


def _pool_for_prompt(items: list[Item]) -> list[dict[str, Any]]:
    pool = []
    for it in items:
        summary = it.summary or it.raw_text or ""
        if len(summary) > 800:
            summary = summary[:800].rsplit(" ", 1)[0] + "..."
        pool.append({
            "item_id": it.id,
            "source": it.source,
            "title": it.title,
            "url": it.url,
            "published_at": it.published_at,
            "tags": it.tags,
            "priority_tag": it.priority_tag,
            "score": it.score,
            "cluster_id": it.cluster_id,
            "filter_rationale": it.rationale,
            "summary": summary,
        })
    return pool


def _extract_json_object(text: str) -> dict:
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in compose response: {text[:200]}")
    return json.loads(text[start:end + 1])


def compose(
    pool: list[Item],
    reviewed_count: int,
    client: Anthropic | None = None,
    model: str | None = None,
) -> tuple[Digest, dict[str, Any]]:
    if not pool:
        raise ValueError("Cannot compose from an empty pool")

    client = client or Anthropic()
    model = model or os.environ.get("COMPOSE_MODEL", DEFAULT_COMPOSE_MODEL)
    system_prompt = _load_system_prompt()

    prompt_pool = _pool_for_prompt(pool)
    user_message = (
        f"Here are today's {len(prompt_pool)} top-scored items (sorted by score). "
        "Write the digest per the schema. Every item_id you reference MUST appear below.\n\n"
        f"```json\n{json.dumps(prompt_pool, indent=2)}\n```"
    )

    log.info("Compose: writing digest from %d items with %s", len(prompt_pool), model)
    response = client.messages.create(
        model=model,
        max_tokens=3000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    try:
        payload = _extract_json_object(text)
    except (ValueError, json.JSONDecodeError) as exc:
        log.error("Compose response parse failed: %s\nRaw: %s", exc, text[:1000])
        raise

    by_id = {it.id: it for it in pool}

    def _build(entry: dict, body_key: str) -> ComposedItem | None:
        item_id = entry.get("item_id")
        it = by_id.get(item_id)
        if it is None:
            log.warning("Compose referenced unknown item_id %s", item_id)
            return None
        return ComposedItem(
            item_id=item_id,
            headline=entry.get("headline", it.title).strip(),
            url=it.url,
            source=it.source,
            read_time_min=int(entry.get("read_time_min", 3)),
            body=entry.get(body_key, "").strip(),
        )

    what_matters = [
        x for x in (_build(e, "why_it_matters") for e in payload.get("what_matters_today", []))
        if x is not None
    ][:3]

    quick_hits_raw = payload.get("quick_hits", [])
    quick_hits: list[ComposedItem] = []
    for entry in quick_hits_raw:
        item_id = entry.get("item_id")
        it = by_id.get(item_id)
        if it is None:
            continue
        quick_hits.append(ComposedItem(
            item_id=item_id,
            headline=entry.get("line", it.title).strip(),
            url=it.url,
            source=it.source,
            read_time_min=2,
            body="",
        ))
    quick_hits = quick_hits[:8]

    deeper = payload.get("deeper_look")
    deeper_item = _build(deeper, "pitch") if deeper else None
    if deeper_item:
        deeper_item.read_time_min = int((deeper or {}).get("read_time_min", 12))

    digest = Digest(
        subject_headline=payload.get("subject_headline", "Daily update").strip(),
        top_story_preview=payload.get("top_story_preview", "").strip(),
        what_matters_today=what_matters,
        quick_hits=quick_hits,
        deeper_look=deeper_item,
        reviewed_count=reviewed_count,
    )
    stats = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    log.info(
        "Compose: subject=%r top=%d quick=%d deeper=%s (in=%d out=%d)",
        digest.subject_headline,
        len(digest.what_matters_today),
        len(digest.quick_hits),
        "yes" if digest.deeper_look else "no",
        response.usage.input_tokens,
        response.usage.output_tokens,
    )
    return digest, stats
