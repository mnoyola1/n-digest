"""Persistent state for n-digest: 14-day rolling dedup + last-digest pointer."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)

DEDUP_WINDOW_DAYS = 14


@dataclass
class LastDigest:
    date: str = ""
    url: str = ""
    top_headline: str = ""
    subject: str = ""


@dataclass
class DigestState:
    seen_hashes: dict[str, str] = field(default_factory=dict)  # id -> ISO8601 expiry
    last_digest: LastDigest = field(default_factory=LastDigest)

    def mark_seen(self, item_id: str) -> None:
        expires = (datetime.now(timezone.utc) + timedelta(days=DEDUP_WINDOW_DAYS)).isoformat()
        self.seen_hashes[item_id] = expires

    def is_seen(self, item_id: str) -> bool:
        return item_id in self.seen_hashes

    def prune(self) -> int:
        """Drop expired dedup entries. Returns count removed."""
        now = datetime.now(timezone.utc)
        expired = [
            k for k, v in self.seen_hashes.items()
            if _parse_iso(v) and _parse_iso(v) < now  # type: ignore[operator]
        ]
        for k in expired:
            del self.seen_hashes[k]
        return len(expired)


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def load(path: str) -> DigestState:
    if not os.path.exists(path):
        log.info("No state file at %s; starting fresh", path)
        return DigestState()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read state file %s: %s; starting fresh", path, exc)
        return DigestState()

    seen = raw.get("seen_hashes", {})
    if isinstance(seen, list):
        # migrate older list-of-dicts format
        seen = {entry["hash"]: entry.get("expires_at", "") for entry in seen if isinstance(entry, dict)}

    last = raw.get("last_digest") or {}
    state = DigestState(
        seen_hashes=dict(seen),
        last_digest=LastDigest(
            date=last.get("date", ""),
            url=last.get("url", ""),
            top_headline=last.get("top_headline", ""),
            subject=last.get("subject", ""),
        ),
    )
    removed = state.prune()
    if removed:
        log.info("Pruned %d expired dedup entries", removed)
    return state


def save(path: str, state: DigestState) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload: dict[str, Any] = {
        "seen_hashes": state.seen_hashes,
        "last_digest": {
            "date": state.last_digest.date,
            "url": state.last_digest.url,
            "top_headline": state.last_digest.top_headline,
            "subject": state.last_digest.subject,
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
