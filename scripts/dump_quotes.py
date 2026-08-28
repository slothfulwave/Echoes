"""Dev-only helper: snapshot scheduled quotes to a JSON file for manual review.

Builds the playlist exactly as ``echoes run`` would, then writes the quotes
scheduled per date to ``quotes_snapshot.json`` at the repo root so it can be
opened and read directly.

This mirrors ``echoes run --dry-run``: it may call Notion to build or rebuild
a pool, but it never writes to ``state/`` and never sends anything. Safe to
run repeatedly against real credentials without disturbing the committed
playlist.

Not part of the ``echoes`` package or its CLI, and not covered by pytest -
the test suite runs without network access (see CLAUDE.md), and this script
calls real Notion. It is a manual inspection tool, not a regression test.

Usage:
    python scripts/dump_quotes.py                        today onward, full horizon
    python scripts/dump_quotes.py --days 7                today plus the next 6 days
    python scripts/dump_quotes.py --start 2026-09-01 --days 7
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from echoes.collect import NotionAPI, QuoteCollector
from echoes.config import Settings, project_root
from echoes.errors import EchoesError
from echoes.logging_setup import configure_logging, get_logger
from echoes.models import ISO_DATE, Playlist
from echoes.playlist import PlaylistService, StateStore

logger = get_logger(__name__)

OUTPUT_PATH = project_root() / "quotes_snapshot.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start", default=None, help="ISO date to start from (default: today)."
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help="How many days to dump (default: the full horizon of the built playlist).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging("INFO")

    try:
        settings = Settings.from_env()
    except EchoesError as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    start = date.fromisoformat(args.start) if args.start else date.today()
    store = StateStore(settings.state_dir)

    try:
        with NotionAPI(
            settings.notion.api_key,
            api_version=settings.notion.api_version,
            timeout_seconds=settings.notion.timeout_seconds,
            max_retries=settings.notion.max_retries,
        ) as api:
            collector = QuoteCollector(
                api, settings.notion, attribution_separator=settings.attribution_separator
            )
            service = PlaylistService(store, collector, settings)
            result = service.prepare_for(start)
    except EchoesError as exc:
        logger.error("Could not prepare the playlist: %s", exc)
        return 2

    end = (
        start + timedelta(days=args.days - 1)
        if args.days
        else _horizon_end(result.playlist, start)
    )
    if end < start:
        logger.error("Nothing is scheduled on or after %s", start.strftime(ISO_DATE))
        return 1

    snapshot = build_snapshot(service, result.playlist, start, end)
    OUTPUT_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info("Wrote %d day(s) of quotes to %s", len(snapshot["days"]), OUTPUT_PATH)
    return 0


def _horizon_end(playlist: Playlist, start: date) -> date:
    """Default end date: the furthest date any pool currently covers."""
    ends = [s.last_date for s in playlist.pools.values() if s.last_date is not None]
    return max(ends) if ends else start


def build_snapshot(
    service: PlaylistService, playlist: Playlist, start: date, end: date
) -> dict:
    """Quotes scheduled per date, ``start`` through ``end`` inclusive."""
    days: dict[str, list[dict]] = {}
    day = start
    while day <= end:
        bundle = service.bundle_for(playlist, day)
        days[day.strftime(ISO_DATE)] = [quote.to_dict() for quote in bundle.quotes]
        day += timedelta(days=1)

    return {
        "generated_at": date.today().strftime(ISO_DATE),
        "range": {"start": start.strftime(ISO_DATE), "end": end.strftime(ISO_DATE)},
        "days": days,
    }


if __name__ == "__main__":
    raise SystemExit(main())
