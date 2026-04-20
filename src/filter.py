"""First-stage curation: score and cluster the pool with Claude Haiku."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from .fetch import Item

log = logging.getLogger(__name__)

DEFAULT_FILTER_MODEL = "claude-haiku-4-5"
MAX_ITEMS_PER_CALL = 90
MIN_SCORE_KEEP = 5
TOP_N_DEFAULT = 20
MAX_OUTPUT_TOKENS = 16000


def _load_system_prompt() -> str:
    path = Path(__file__).resolve().parent.parent / "prompts" / "filter_system.md"
    return path.read_text(encoding="utf-8")


def _compact_item(item: Item) -> dict[str, Any]:
    """Shrink an Item to the minimum the model needs to score it."""
    summary = item.summary or item.raw_text or ""
    if len(summary) > 600:
        summary = summary[:600].rsplit(" ", 1)[0] + "..."
    return {
        "item_id": item.id,
        "source": item.source,
        "tags": item.tags,
        "title": item.title,
        "summary": summary,
    }


def _extract_json_array(text: str) -> list[dict]:
    """Pull out the first JSON array in the response; tolerate prose wrapping and
    truncated output by finding the last complete object and closing the array."""
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\[.*)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    start = text.find("[")
    if start == -1:
        raise ValueError(f"No JSON array opener in response: {text[:200]}")
    body = text[start:]
    end = body.rfind("]")
    if end != -1:
        try:
            return json.loads(body[:end + 1])
        except json.JSONDecodeError:
            pass
    # Fallback: the array was truncated. Walk forward tracking depth; keep the
    # last balanced object we saw.
    depth = 0
    in_str = False
    escape = False
    last_complete = -1
    saw_object = False
    for i, ch in enumerate(body[1:], start=1):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                saw_object = True
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and saw_object:
                last_complete = i
    if last_complete == -1:
        raise ValueError(f"No complete JSON objects in truncated array: {body[:300]}")
    repaired = body[:last_complete + 1] + "]"
    return json.loads(repaired)


def score_pool(
    items: list[Item],
    client: Anthropic | None = None,
    model: str | None = None,
    top_n: int = TOP_N_DEFAULT,
) -> tuple[list[Item], dict[str, Any]]:
    """Score every item, attach score/priority/cluster/rationale, return top N plus stats.

    Items that the model doesn't score (or scores below MIN_SCORE_KEEP) are dropped.
    """
    if not items:
        return [], {"model": None, "input_tokens": 0, "output_tokens": 0, "kept": 0, "total": 0}

    client = client or Anthropic()
    model = model or os.environ.get("FILTER_MODEL", DEFAULT_FILTER_MODEL)
    system_prompt = _load_system_prompt()

    # Trim the pool if absurdly large to stay in budget.
    pool = items[:MAX_ITEMS_PER_CALL]
    compact = [_compact_item(it) for it in pool]

    user_message = (
        "Here are today's candidate items. Score each one per the rubric. "
        "Return ONLY a JSON array.\n\n"
        f"Total items: {len(compact)}\n\n"
        f"```json\n{json.dumps(compact, indent=2)}\n```"
    )

    log.info("Filter: scoring %d items with %s", len(compact), model)
    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    try:
        scored = _extract_json_array(text)
    except (ValueError, json.JSONDecodeError) as exc:
        log.error("Filter response parse failed: %s\nRaw: %s", exc, text[:500])
        scored = []

    by_id = {it.id: it for it in pool}
    enriched: list[Item] = []
    for entry in scored:
        item_id = entry.get("item_id")
        it = by_id.get(item_id)
        if it is None:
            continue
        try:
            it.score = float(entry.get("score", 0))
        except (TypeError, ValueError):
            it.score = 0.0
        it.priority_tag = entry.get("priority_tag")
        it.cluster_id = entry.get("cluster_id")
        it.rationale = entry.get("rationale")
        enriched.append(it)

    # Drop low scorers and dedupe clusters by keeping the highest-scoring member.
    kept = [it for it in enriched if (it.score or 0) >= MIN_SCORE_KEEP]
    kept.sort(key=lambda x: (x.score or 0), reverse=True)

    clusters_seen: set[str] = set()
    deduped: list[Item] = []
    for it in kept:
        cluster = it.cluster_id or it.id
        if cluster in clusters_seen:
            continue
        clusters_seen.add(cluster)
        deduped.append(it)

    top = deduped[:top_n]
    stats = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "total_scored": len(enriched),
        "kept_after_threshold": len(kept),
        "kept_after_cluster": len(deduped),
        "returned": len(top),
    }
    log.info(
        "Filter: scored=%d kept=%d clusters=%d returned=%d (in=%d out=%d tokens)",
        len(enriched), len(kept), len(deduped), len(top),
        response.usage.input_tokens, response.usage.output_tokens,
    )
    return top, stats
