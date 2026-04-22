"""Orchestrator for n-digest.

Usage:
  python -m src.main --dry-run        # fetch + curate + render, no send, no state write
  python -m src.main --send-to-self   # full pipeline, real send
  python -m src.main                  # scheduled run (GitHub Actions)

Scheduled runs are gated by a DST-aware cron matcher, not by wall-clock time.
GitHub Actions free-tier cron drift routinely exceeds 1-2 hours on popular cron
minutes, so a time-window guard would silently drop most runs. Instead, both
09:30 UTC and 10:30 UTC cron entries fire daily, and the guard accepts whichever
one targets the DST offset currently in effect in New York. The email may land
anywhere from 5:30 AM ET (best case, rare) to late morning (heavy drift), but
it lands exactly once per weekday in the correct timezone.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from anthropic import Anthropic

from . import archive, compose, fetch, filter as filter_mod, render, send, state

log = logging.getLogger("n-digest")

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = REPO_ROOT / "src" / "sources.yml"
STATE_PATH = REPO_ROOT / "state" / "state.json"
OUT_PREVIEW = REPO_ROOT / "out" / "preview.html"

# Claude pricing per 1M tokens (USD) for rough cost logging.
PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-opus-4-7": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
}


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_dotenv_if_present() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _et_now() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


# Map UTC cron entries (must match .github/workflows/daily-digest.yml) to the
# DST offset they are intended for. Each entry targets 5:30 AM America/New_York.
SCHEDULED_CRON_TO_OFFSET = {
    "30 9 * * 1-5":  -4 * 3600,  # EDT (UTC-4): 09:30 UTC == 05:30 EDT
    "30 10 * * 1-5": -5 * 3600,  # EST (UTC-5): 10:30 UTC == 05:30 EST
}


def _should_run_on_schedule() -> bool:
    """Decide whether this invocation should proceed.

    Two modes:

    1. **GitHub-scheduled run** (``SCHEDULED_CRON`` env set): accept only if the
       fired cron entry targets the DST offset currently in effect in New York.
       This guarantees exactly one send per weekday regardless of GitHub Actions
       scheduler drift (which is frequently 1-2 hours on free-tier runners).

    2. **Local / manual run without --ignore-schedule** (no SCHEDULED_CRON):
       fall back to a time-window check centered on 5:30 AM ET (+/-15 min) so
       developers can't accidentally send from `python -m src.main` mid-day.
    """
    now = _et_now()
    if now.weekday() > 4:  # Sat/Sun
        return False

    fired_cron = os.environ.get("SCHEDULED_CRON", "").strip()
    if fired_cron:
        target_offset = SCHEDULED_CRON_TO_OFFSET.get(fired_cron)
        if target_offset is None:
            log.warning("Unknown SCHEDULED_CRON=%r; refusing to send", fired_cron)
            return False
        current_offset = int(now.utcoffset().total_seconds())
        if current_offset != target_offset:
            log.info(
                "Skipping: cron %r targets UTC%+d but ET is currently UTC%+d (DST mismatch)",
                fired_cron, target_offset // 3600, current_offset // 3600,
            )
            return False
        log.info("Schedule matched: cron %r for current ET offset UTC%+d", fired_cron, current_offset // 3600)
        return True

    # Fallback: manual / local invocation without --ignore-schedule.
    target_minutes = 5 * 60 + 30  # 05:30 ET
    current_minutes = now.hour * 60 + now.minute
    return abs(current_minutes - target_minutes) <= 15


def _estimate_cost(model: str, in_tokens: int, out_tokens: int) -> float:
    rates = PRICING.get(model)
    if not rates:
        return 0.0
    return (in_tokens / 1_000_000) * rates["input"] + (out_tokens / 1_000_000) * rates["output"]


def run(dry_run: bool = False, send_override: str | None = None, ignore_schedule: bool = False) -> int:
    _load_dotenv_if_present()

    if not ignore_schedule and not dry_run and not send_override:
        if not _should_run_on_schedule():
            # Detailed reason was already logged by _should_run_on_schedule().
            log.info("Run rejected by schedule guard; exiting cleanly (et=%s)", _et_now().isoformat())
            return 0

    now_et = _et_now()
    today_str = now_et.strftime("%Y-%m-%d")
    log.info("n-digest run starting: et=%s dry_run=%s", now_et.isoformat(), dry_run)

    items, fetch_stats = fetch.fetch_all(str(SOURCES_PATH))
    log.info(
        "Fetch: %d unique items from %d sources (failures=%d)",
        fetch_stats["total_unique"],
        len(fetch_stats["per_source"]),
        len(fetch_stats["failures"]),
    )
    if fetch_stats["failures"]:
        for f in fetch_stats["failures"]:
            log.warning("  source failure: %s -> %s", f["source"], f["error"])

    st = state.load(str(STATE_PATH))
    before_dedup = len(items)
    items = [it for it in items if not st.is_seen(it.id)]
    log.info("Dedup: %d -> %d items after removing seen", before_dedup, len(items))

    if not items:
        log.warning("Nothing new to send today; exiting without email")
        return 0

    client = Anthropic()
    top_items, filter_stats = filter_mod.score_pool(items, client=client)
    if not top_items:
        log.warning("Filter kept zero items; exiting without email")
        return 0

    digest, compose_stats = compose.compose(top_items, reviewed_count=before_dedup, client=client)

    yesterday_url = st.last_digest.url if st.last_digest.url else ""
    html = render.render_html(digest, now_et, yesterday_url=yesterday_url)
    subject = render.render_subject(digest, now_et)

    filter_cost = _estimate_cost(filter_stats.get("model", ""), filter_stats["input_tokens"], filter_stats["output_tokens"])
    compose_cost = _estimate_cost(compose_stats.get("model", ""), compose_stats["input_tokens"], compose_stats["output_tokens"])
    log.info(
        "LLM cost estimate: filter=$%.4f compose=$%.4f total=$%.4f",
        filter_cost, compose_cost, filter_cost + compose_cost,
    )

    OUT_PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    OUT_PREVIEW.write_text(html, encoding="utf-8")
    log.info("Wrote preview to %s", OUT_PREVIEW)
    log.info("Subject: %s", subject)

    if dry_run:
        log.info("Dry run: skipping send, archive, and state write")
        return 0

    archive.write_archive(today_str, html)
    archive.rebuild_index()

    to_email = send_override or os.environ.get("DIGEST_TO_EMAIL") or "mnoyola1@gmail.com"
    try:
        message_id = send.send_email(subject=subject, html=html, to_email=to_email)
    except Exception as exc:
        log.exception("Send failed: %s", exc)
        return 2

    archive_base = os.environ.get("DIGEST_ARCHIVE_BASE_URL") or None
    digest_url = archive.archive_url_for(today_str, archive_base)
    st.last_digest = state.LastDigest(
        date=today_str,
        url=digest_url,
        top_headline=digest.subject_headline,
        subject=subject,
    )
    for it in top_items:
        st.mark_seen(it.id)
    for it in items:
        st.mark_seen(it.id)

    state.save(str(STATE_PATH), st)
    log.info("Done. message_id=%s digest_url=%s", message_id, digest_url)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the n-digest daily AI email.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch, curate, render to out/preview.html, but do not send")
    parser.add_argument("--send-to-self", action="store_true", help="Force send to DIGEST_TO_EMAIL regardless of schedule")
    parser.add_argument("--to", default=None, help="Override recipient email")
    parser.add_argument("--ignore-schedule", action="store_true", help="Run even if current ET time isn't 6:30 AM")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    to_override = args.to if args.to else (os.environ.get("DIGEST_TO_EMAIL") if args.send_to_self else None)
    return run(
        dry_run=args.dry_run,
        send_override=to_override if (args.send_to_self or args.to) else None,
        ignore_schedule=args.ignore_schedule or args.send_to_self or args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
