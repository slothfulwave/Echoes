"""Command line interface.

Commands::

    echoes run       Daily run: prepare, deliver, and refresh if it is Sunday.
    echoes refresh   Weekly refresh only (manual).
    echoes collect   Read both pools from Notion and print what was found.
                     Read-only - touches no state. Use this first to verify
                     the Notion filters and callout parsing against real data.
    echoes show      Print the quotes scheduled for a date, straight from the
                     playlist. No Notion calls.

Exit codes: ``0`` success, ``1`` run completed with alerts, ``2`` fatal error.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from echoes import __version__
from echoes.collect import NotionAPI, QuoteCollector
from echoes.config import Settings
from echoes.deliver.formatter import format_quote
from echoes.errors import ConfigurationError, EchoesError
from echoes.logging_setup import configure_logging, get_logger
from echoes.models import ISO_DATE, PoolName
from echoes.pipeline import run_daily, run_refresh
from echoes.playlist import PlaylistService, StateStore

logger = get_logger(__name__)

EXIT_OK = 0
EXIT_ALERTS = 1
EXIT_FATAL = 2


def build_parser() -> argparse.ArgumentParser:
    # Defined once and attached to both the top-level parser and every
    # subparser, so --log-level/--dry-run work in either position:
    #   echoes --dry-run run   and   echoes run --dry-run
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override LOG_LEVEL for this invocation.",
    )
    common.add_argument(
        "--dry-run",
        action="store_true",
        help="Do everything except sending messages and writing state.",
    )

    parser = argparse.ArgumentParser(
        prog="echoes",
        description="Echoes - the voices of books and ideas, returning in rhythm.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"echoes {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Daily run: prepare, deliver, refresh if due.", parents=[common])
    sub.add_parser("refresh", help="Run the weekly refresh on its own.", parents=[common])
    sub.add_parser("collect", help="Read both pools from Notion (read-only).", parents=[common])

    show = sub.add_parser("show", help="Show the quotes scheduled for a date.", parents=[common])
    show.add_argument("--date", dest="on", default=None, help="ISO date (default: today).")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Logging is configured twice on purpose: once with a safe default so that
    # configuration errors are visible, then again once LOG_LEVEL is known.
    configure_logging(args.log_level or "INFO")

    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        logger.error("Configuration error: %s", exc)
        return EXIT_FATAL

    configure_logging(args.log_level or settings.log_level)

    if args.dry_run:
        settings = _with_dry_run(settings)

    try:
        match args.command:
            case "run":
                report = run_daily(settings)
                return EXIT_OK if report.succeeded else EXIT_ALERTS
            case "refresh":
                run_refresh(settings)
                return EXIT_OK
            case "collect":
                return _command_collect(settings)
            case "show":
                return _command_show(settings, args.on)
            case _:  # pragma: no cover - argparse enforces the choices
                logger.error("Unknown command %r", args.command)
                return EXIT_FATAL
    except EchoesError as exc:
        logger.error("%s: %s", type(exc).__name__, exc)
        return EXIT_FATAL
    except KeyboardInterrupt:  # pragma: no cover
        logger.warning("Interrupted")
        return EXIT_FATAL


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def _command_collect(settings: Settings) -> int:
    """Read-only inspection of both pools."""
    with NotionAPI(
        settings.notion.api_key,
        api_version=settings.notion.api_version,
        timeout_seconds=settings.notion.timeout_seconds,
        max_retries=settings.notion.max_retries,
    ) as api:
        collector = QuoteCollector(
            api, settings.notion, attribution_separator=settings.attribution_separator
        )
        books = collector.collect_books()
        standalone = collector.collect_standalone()

    for pool_name, quotes in (("BOOKS", books), ("STANDALONE", standalone)):
        print(f"\n{pool_name} POOL - {len(quotes)} quote(s)")
        print("-" * 70)
        for index, quote in enumerate(quotes, start=1):
            print(f"{index:>4}. {format_quote(quote, book_separator=settings.book_separator)}")

    books_days = -(-len(books) // settings.quotes_per_day_books) if books else 0
    standalone_days = (
        -(-len(standalone) // settings.quotes_per_day_standalone)
        if standalone and settings.quotes_per_day_standalone
        else 0
    )
    print(
        f"\nHorizon at the configured rate: books ~{books_days} day(s), "
        f"standalone ~{standalone_days} day(s). "
        "The pools recycle independently, so these differ by design."
    )
    return EXIT_OK


def _command_show(settings: Settings, on: str | None) -> int:
    """Print a scheduled day without contacting Notion."""
    try:
        day = date.fromisoformat(on) if on else datetime.now(settings.timezone).date()
    except ValueError:
        logger.error("--date must be an ISO date (YYYY-MM-DD), got %r", on)
        return EXIT_FATAL

    store = StateStore(settings.state_dir)
    playlist = store.load_playlist()

    if not playlist.pools:
        print("No playlist exists yet. Run 'echoes run' to prepare one.")
        return EXIT_OK

    service = PlaylistService(store, None, settings)  # type: ignore[arg-type]
    bundle = service.bundle_for(playlist, day)

    print(f"\nScheduled for {day.strftime(ISO_DATE)}")
    print("-" * 70)
    if bundle.is_empty:
        print("Nothing scheduled for this date.")
    else:
        for index, quote in enumerate(bundle.quotes, start=1):
            print(f"{index}. {format_quote(quote, book_separator=settings.book_separator)}")

    for pool in (PoolName.BOOKS, PoolName.STANDALONE):
        schedule = playlist.get(pool)
        if schedule and not schedule.is_empty:
            print(
                f"\n{pool}: cycle {schedule.cycle}, {schedule.per_day}/day, "
                f"{schedule.total_quotes} quote(s) through {schedule.last_date}"
            )
    return EXIT_OK


def _with_dry_run(settings: Settings) -> Settings:
    """Return a copy with ``dry_run`` forced on (Settings is frozen)."""
    import dataclasses

    return dataclasses.replace(settings, dry_run=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
