"""Class 2.1 - the weekly refresh.

Append-only and non-destructive: newly found quotes go to the future of the
playlist, and already-scheduled days are never reordered or replaced. The one
exception is a short tail day still in the future, which is topped up to full
size - additive, never a replacement.

Detection is a set difference over Notion block UUIDs, which makes the refresh
self-healing: because the seen index is a complete record rather than a moving
cursor, a missed week is caught up automatically on the next run.
"""

from __future__ import annotations

from datetime import date

from echoes.collect import NotionAPI, QuoteCollector
from echoes.config import Settings
from echoes.logging_setup import get_logger
from echoes.models import ISO_DATE, Playlist, SeenIndex
from echoes.playlist import PlaylistService, StateStore
from echoes.playlist.service import RefreshResult

logger = get_logger(__name__)


def perform_refresh(
    service: PlaylistService,
    store: StateStore,
    settings: Settings,
    playlist: Playlist,
    seen: SeenIndex,
    today: date,
) -> RefreshResult:
    """Diff Notion against the seen index and append anything new."""
    result = service.refresh(playlist, seen, today)

    if result.total_new == 0:
        logger.info("Refresh complete: nothing new since the last check")
        return result

    if settings.dry_run:
        logger.info("DRY RUN - %d new quote(s) found but not persisted", result.total_new)
        return result

    store.save_playlist(playlist)
    store.save_seen(seen)
    logger.info(
        "Refresh complete: %d new quote(s) appended (%s)",
        result.total_new,
        ", ".join(f"{pool}={count}" for pool, count in result.new_by_pool.items()),
    )
    return result


def run_refresh(settings: Settings) -> RefreshResult:
    """Run the refresh on its own, outside the daily run (manual/testing)."""
    today = date.today() if settings.timezone is None else _today(settings)
    logger.info("Manual refresh for %s", today.strftime(ISO_DATE))

    store = StateStore(settings.state_dir)
    playlist = store.load_playlist()
    seen = store.load_seen()

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
        return perform_refresh(service, store, settings, playlist, seen, today)


def _today(settings: Settings) -> date:
    from datetime import datetime

    return datetime.now(settings.timezone).date()
