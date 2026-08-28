"""Playlist orchestration - the daily "prepare and pick" decision.

The pools are independent. Each one assigns dates forward until its own
quotes run out, then reshuffles and rebuilds on its own, without touching the
other. As a result the books pool and the standalone pool recycle on different
cycles, which is intended.

The fallback (standalone empty -> three book quotes) is resolved at *build*
time, not send time. A standalone pool only truly runs dry when Notion holds no
standalone quotes at all - otherwise it simply reshuffles - so the correct
response is to rebuild the books pool at the fallback rate rather than to
mutate an already-scheduled day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from echoes.collect.collector import QuoteCollector
from echoes.config import Settings
from echoes.logging_setup import get_logger
from echoes.models import DailyBundle, ISO_DATE, Playlist, PoolName, PoolSchedule, Quote, SeenIndex
from echoes.playlist.scheduler import append_schedule, build_schedule, make_rng
from echoes.playlist.state_store import StateStore

logger = get_logger(__name__)


@dataclass(slots=True)
class PreparationResult:
    """Outcome of preparing the playlist for a given day."""

    playlist: Playlist
    seen: SeenIndex
    bundle: DailyBundle
    rebuilt_pools: list[PoolName] = field(default_factory=list)

    @property
    def state_changed(self) -> bool:
        return bool(self.rebuilt_pools)


@dataclass(slots=True)
class RefreshResult:
    """Outcome of the weekly refresh."""

    new_by_pool: dict[PoolName, int] = field(default_factory=dict)
    changed: bool = False

    @property
    def total_new(self) -> int:
        return sum(self.new_by_pool.values())


class PlaylistService:
    """Prepares the playlist and picks the day's quotes."""

    def __init__(self, store: StateStore, collector: QuoteCollector, settings: Settings) -> None:
        self._store = store
        self._collector = collector
        self._settings = settings

    # ------------------------------------------------------------------
    # Daily preparation
    # ------------------------------------------------------------------
    def prepare_for(self, today: date) -> PreparationResult:
        """Ensure both pools cover ``today`` and return the day's quotes.

        May raise :class:`CollectionError` if Notion is unreachable during a
        rebuild. The caller is expected to fall back to the existing playlist.
        """
        playlist = self._store.load_playlist()
        seen = self._store.load_seen()
        rng = make_rng(self._settings.random_seed)
        rebuilt: list[PoolName] = []

        # 1. Standalone first - whether it has any quotes decides the books rate.
        standalone_rate = self._settings.quotes_per_day_standalone
        if standalone_rate > 0:
            if self._ensure_pool(
                playlist, seen, PoolName.STANDALONE, standalone_rate, today, rng,
                self._collector.collect_standalone,
            ):
                rebuilt.append(PoolName.STANDALONE)
        else:
            logger.info("Standalone pool disabled by configuration (QUOTES_PER_DAY_STANDALONE=0)")

        standalone_schedule = playlist.get(PoolName.STANDALONE)
        standalone_has_quotes = bool(standalone_schedule and standalone_schedule.total_quotes > 0)

        # 2. Books rate follows from that.
        books_rate = (
            self._settings.quotes_per_day_books
            if standalone_has_quotes
            else self._settings.quotes_per_day_books_fallback
        )
        if not standalone_has_quotes:
            logger.warning(
                "Standalone pool holds no quotes - falling back to %d book quote(s) per day",
                books_rate,
            )

        if self._ensure_pool(
            playlist, seen, PoolName.BOOKS, books_rate, today, rng, self._collector.collect_books
        ):
            rebuilt.append(PoolName.BOOKS)

        bundle = self.bundle_for(playlist, today)
        return PreparationResult(playlist=playlist, seen=seen, bundle=bundle, rebuilt_pools=rebuilt)

    def bundle_for(self, playlist: Playlist, today: date) -> DailyBundle:
        """Read today's quotes straight out of the playlist - no Notion calls.

        Books come first (each labelled with its title), then the standalone
        quote. This is also the degraded path used when a rebuild failed.
        """
        books = self._quotes_for(playlist, PoolName.BOOKS, today)
        standalone = self._quotes_for(playlist, PoolName.STANDALONE, today)

        quotes: list[Quote] = [*books, *standalone]
        used_fallback = not standalone and len(books) > self._settings.quotes_per_day_books

        if not quotes:
            logger.error("No quotes are scheduled for %s in either pool", today.strftime(ISO_DATE))
        else:
            logger.info(
                "Picked %d quote(s) for %s (%d from books, %d standalone%s)",
                len(quotes), today.strftime(ISO_DATE), len(books), len(standalone),
                ", fallback active" if used_fallback else "",
            )

        return DailyBundle(day=today, quotes=quotes, used_fallback=used_fallback)

    # ------------------------------------------------------------------
    # Weekly refresh
    # ------------------------------------------------------------------
    def refresh(self, playlist: Playlist, seen: SeenIndex, today: date) -> RefreshResult:
        """Find quotes added since the last refresh and append them.

        Detection is a set difference over Notion block UUIDs, so it catches a
        new book page, a new tagged quotes page, and new callouts added to a
        page that already existed - all with one mechanism, and correctly even
        if several refreshes were missed.
        """
        rng = make_rng(self._settings.random_seed)
        result = RefreshResult()

        sources: list[tuple[PoolName, object]] = [
            (PoolName.BOOKS, self._collector.collect_books),
        ]
        if self._settings.quotes_per_day_standalone > 0:
            sources.append((PoolName.STANDALONE, self._collector.collect_standalone))

        for pool, collect in sources:
            current: list[Quote] = collect()  # type: ignore[operator]
            new_quotes = seen.unseen(pool, current)
            result.new_by_pool[pool] = len(new_quotes)

            if not new_quotes:
                logger.info("Refresh: no new quotes in the %s pool", pool)
                continue

            logger.info("Refresh: %d new quote(s) found in the %s pool", len(new_quotes), pool)
            seen.mark_seen(pool, {quote.block_id for quote in new_quotes})

            schedule = playlist.get(pool)
            if schedule is None or schedule.is_empty:
                # Pool had nothing scheduled - start a fresh cycle from tomorrow.
                per_day = self._rate_for(pool, playlist)
                start = date.fromordinal(today.toordinal() + 1)
                playlist.set(
                    pool,
                    build_schedule(
                        new_quotes, per_day=per_day, start_date=start,
                        cycle=(schedule.cycle if schedule else 0) + 1, rng=rng,
                    ),
                )
                logger.info(
                    "Refresh: %s pool was empty - built a new cycle starting %s",
                    pool, start.strftime(ISO_DATE),
                )
                result.changed = True
                continue

            append = append_schedule(schedule, new_quotes, rng=rng, today=today)
            result.changed = result.changed or append.changed

        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _ensure_pool(
        self,
        playlist: Playlist,
        seen: SeenIndex,
        pool: PoolName,
        per_day: int,
        today: date,
        rng,
        collect,
    ) -> bool:
        """Rebuild ``pool`` if it cannot serve ``today``. Returns True if rebuilt."""
        schedule = playlist.get(pool)
        reason = self._rebuild_reason(schedule, per_day, today)

        if reason is None:
            logger.debug("%s pool already covers %s", pool, today.strftime(ISO_DATE))
            return False

        logger.info("Rebuilding %s pool (%s)", pool, reason)
        quotes: list[Quote] = collect()

        if quotes:
            new_count = seen.mark_seen(pool, {quote.block_id for quote in quotes})
            if new_count:
                logger.info("%s pool: %d quote(s) recorded in the seen index", pool, new_count)

        cycle = (schedule.cycle if schedule else 0) + 1
        rebuilt = build_schedule(
            quotes, per_day=per_day, start_date=today, cycle=cycle, rng=rng
        )
        playlist.set(pool, rebuilt)

        if rebuilt.is_empty:
            logger.warning(
                "%s pool rebuilt but Notion returned no quotes - it will be rechecked tomorrow",
                pool,
            )
        else:
            logger.info(
                "%s pool cycle %d: %d quote(s) scheduled across %d day(s), %s to %s",
                pool, cycle, rebuilt.total_quotes, len(rebuilt.days),
                rebuilt.first_date, rebuilt.last_date,
            )
        return True

    @staticmethod
    def _rebuild_reason(schedule: PoolSchedule | None, per_day: int, today: date) -> str | None:
        """Why this pool needs rebuilding, or ``None`` if it does not."""
        if schedule is None:
            return "no schedule exists yet"

        if schedule.per_day != per_day:
            return f"per-day rate changed from {schedule.per_day} to {per_day}"

        if schedule.is_empty:
            if schedule.built_on == today.strftime(ISO_DATE):
                return None  # Already checked today; do not hammer Notion.
            return "pool is empty - rechecking Notion"

        last = schedule.last_date
        first = schedule.first_date
        if last is not None and today > last:
            return f"cycle exhausted on {last.strftime(ISO_DATE)}"
        if first is not None and today < first:
            return f"schedule starts on {first.strftime(ISO_DATE)}, which is after today"

        return None

    def _rate_for(self, pool: PoolName, playlist: Playlist) -> int:
        existing = playlist.get(pool)
        if existing is not None:
            return existing.per_day
        return (
            self._settings.quotes_per_day_books
            if pool is PoolName.BOOKS
            else self._settings.quotes_per_day_standalone
        )

    @staticmethod
    def _quotes_for(playlist: Playlist, pool: PoolName, today: date) -> list[Quote]:
        schedule = playlist.get(pool)
        return schedule.quotes_for(today) if schedule else []
