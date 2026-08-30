"""The daily run - Class 2 (Prepare & Pick) plus Class 2.3 (Failure Safety).

The order of operations encodes the resilience rule: **the message goes out
even when preparation fails.** If Notion is unreachable, the existing playlist
already on disk is used, the quotes are delivered anyway, and a separate alert
explains what went wrong. Failures are never silent.

The weekly refresh runs *after* delivery, so a refresh failure can never stop
that day's quotes from being sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from echoes.collect import NotionAPI, QuoteCollector
from echoes.config import Settings
from echoes.deliver import build_sender
from echoes.deliver.base import Sender
from echoes.errors import CollectionError, DeliveryError, StateError
from echoes.logging_setup import get_logger
from echoes.models import ISO_DATE, DailyBundle, Playlist, SeenIndex
from echoes.pipeline.refresh import perform_refresh
from echoes.playlist import PlaylistService, StateStore

logger = get_logger(__name__)


@dataclass(slots=True)
class RunReport:
    """What happened during the run - drives the exit code and the alert."""

    delivered: bool = False
    degraded: bool = False
    refreshed: bool = False
    quotes_sent: int = 0
    alerts: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.delivered and not self.alerts

    def add_alert(self, message: str) -> None:
        self.alerts.append(message)


def run_daily(settings: Settings) -> RunReport:
    """Execute one daily run. Returns a report; raises only on fatal errors."""
    now = datetime.now(settings.timezone)
    today = now.date()
    report = RunReport()

    logger.info("=" * 70)
    logger.info("Echoes daily run for %s (%s)", today.strftime(ISO_DATE), settings.timezone_name)
    logger.info(settings.describe())
    logger.info("=" * 70)

    store = StateStore(settings.state_dir)

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
        sender: Sender = build_sender(settings)

        try:
            playlist, seen, bundle = _prepare(service, store, settings, today, report)
            _deliver(sender, bundle, report)

            if report.delivered and settings.sunday_refresh_enabled:
                _maybe_refresh(service, store, settings, playlist, seen, today, report)
        finally:
            sender.close()

        _raise_alerts(sender, report, settings)

    _log_summary(report, today)
    return report


# ----------------------------------------------------------------------
# Phases
# ----------------------------------------------------------------------
def _prepare(
    service: PlaylistService,
    store: StateStore,
    settings: Settings,
    today,
    report: RunReport,
) -> tuple[Playlist, SeenIndex, DailyBundle]:
    """Prepare the playlist, degrading to the existing one if Notion fails."""
    try:
        result = service.prepare_for(today)

        if result.state_changed and not settings.dry_run:
            store.save_playlist(result.playlist)
            store.save_seen(result.seen)
        elif result.state_changed:
            logger.info("DRY RUN - playlist rebuild not persisted")

        return result.playlist, result.seen, result.bundle

    except (CollectionError, StateError) as exc:
        report.degraded = True
        message = (
            f"Echoes couldn't refresh quotes today ({type(exc).__name__}). "
            "Continuing with the existing playlist."
        )
        report.add_alert(message)
        logger.error("Preparation failed: %s", exc)
        logger.warning("Falling back to the playlist already on disk")

        try:
            playlist = store.load_playlist()
        except StateError as state_exc:
            logger.error("Fallback failed - the playlist on disk is unusable: %s", state_exc)
            return Playlist(), SeenIndex(), DailyBundle(day=today, quotes=[])

        try:
            seen = store.load_seen()
        except StateError:
            seen = SeenIndex()

        return playlist, seen, service.bundle_for(playlist, today)


def _deliver(sender: Sender, bundle: DailyBundle, report: RunReport) -> None:
    """Send the day's message."""
    if bundle.is_empty:
        message = (
            f"Echoes had no quotes scheduled for {bundle.day.strftime(ISO_DATE)} "
            "and sent nothing today."
        )
        report.add_alert(message)
        logger.error(message)
        return

    try:
        sender.send_daily(bundle)
        report.delivered = True
        report.quotes_sent = len(bundle.quotes)
    except DeliveryError as exc:
        report.add_alert(f"Echoes could not deliver today's quotes: {exc}")
        logger.error("Delivery failed: %s", exc)


def _maybe_refresh(
    service: PlaylistService,
    store: StateStore,
    settings: Settings,
    playlist: Playlist,
    seen: SeenIndex,
    today,
    report: RunReport,
) -> None:
    """Run the weekly refresh, but only on the configured weekday."""
    if today.weekday() != settings.refresh_weekday:
        logger.debug("Not the refresh weekday - skipping the weekly refresh")
        return

    logger.info("-" * 70)
    logger.info("Weekly refresh (runs after delivery)")

    try:
        result = perform_refresh(service, store, settings, playlist, seen, today)
        report.refreshed = result.changed
    except (CollectionError, StateError) as exc:
        report.degraded = True
        report.add_alert(
            "Echoes couldn't check Notion for new quotes this week. "
            "The existing playlist is unaffected."
        )
        logger.error("Weekly refresh failed: %s", exc)


def _raise_alerts(sender: Sender, report: RunReport, settings: Settings) -> None:
    """Send accumulated alerts. Alerting is best-effort and never raises."""
    if not report.alerts:
        return
    if not settings.alerts_enabled:
        logger.warning("Alerts disabled; %d alert(s) suppressed", len(report.alerts))
        return

    for message in report.alerts:
        try:
            sender.send_alert(message)
        except Exception as exc:
            logger.error("Alert delivery raised unexpectedly (%s): %s", exc, message)


def _log_summary(report: RunReport, today) -> None:
    logger.info("=" * 70)
    logger.info(
        "Run summary for %s: delivered=%s quotes=%d degraded=%s refreshed=%s alerts=%d",
        today.strftime(ISO_DATE), report.delivered, report.quotes_sent,
        report.degraded, report.refreshed, len(report.alerts),
    )
    logger.info("=" * 70)
