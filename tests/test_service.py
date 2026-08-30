"""Playlist service tests - fallback, independent pools, refresh detection."""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import StubCollector, make_quote

from echoes.models import PoolName
from echoes.playlist import PlaylistService, StateStore


def _service(settings, collector) -> tuple[PlaylistService, StateStore]:
    store = StateStore(settings.state_dir)
    return PlaylistService(store, collector, settings), store


def test_first_run_builds_both_pools(settings, today, book_quotes, standalone_quotes):
    collector = StubCollector(book_quotes, standalone_quotes)
    service, _ = _service(settings, collector)

    result = service.prepare_for(today)

    assert set(result.rebuilt_pools) == {PoolName.BOOKS, PoolName.STANDALONE}
    assert len(result.bundle.quotes) == 3
    assert not result.bundle.used_fallback


def test_bundle_is_two_books_then_one_standalone(settings, today, book_quotes, standalone_quotes):
    service, _ = _service(settings, StubCollector(book_quotes, standalone_quotes))

    quotes = service.prepare_for(today).bundle.quotes

    assert [q.pool for q in quotes] == [PoolName.BOOKS, PoolName.BOOKS, PoolName.STANDALONE]


def test_empty_standalone_pool_triggers_the_three_book_fallback(settings, today, book_quotes):
    service, _ = _service(settings, StubCollector(book_quotes, []))

    result = service.prepare_for(today)

    assert len(result.bundle.quotes) == 3
    assert all(q.pool is PoolName.BOOKS for q in result.bundle.quotes)
    assert result.bundle.used_fallback
    assert result.playlist.get(PoolName.BOOKS).per_day == 3


def test_standalone_returning_rebuilds_books_at_the_normal_rate(settings, today, book_quotes):
    """The fallback is resolved at build time, so the rate change forces a rebuild.

    Recovery happens on the next run, not the same one: an empty pool is only
    rechecked once per day so a genuinely empty Notion does not get hammered.
    """
    collector = StubCollector(book_quotes, [])
    service, store = _service(settings, collector)

    first = service.prepare_for(today)
    store.save_playlist(first.playlist)
    store.save_seen(first.seen)
    assert first.playlist.get(PoolName.BOOKS).per_day == 3

    collector.standalone = [make_quote(1, PoolName.STANDALONE, None)]
    second = service.prepare_for(today + timedelta(days=1))

    assert second.playlist.get(PoolName.BOOKS).per_day == 2
    assert PoolName.BOOKS in second.rebuilt_pools
    assert len(second.bundle.quotes) == 3
    assert not second.bundle.used_fallback


def test_a_prepared_pool_is_not_refetched(settings, today, book_quotes, standalone_quotes):
    collector = StubCollector(book_quotes, standalone_quotes)
    service, store = _service(settings, collector)

    first = service.prepare_for(today)
    store.save_playlist(first.playlist)
    store.save_seen(first.seen)
    calls = (collector.books_calls, collector.standalone_calls)

    second = service.prepare_for(today + timedelta(days=1))

    assert (collector.books_calls, collector.standalone_calls) == calls
    assert second.rebuilt_pools == []
    assert len(second.bundle.quotes) == 3


def test_pools_recycle_independently(settings, today, book_quotes, standalone_quotes):
    """4 standalone quotes at 1/day exhaust well before 10 book quotes at 2/day."""
    collector = StubCollector(book_quotes, standalone_quotes)
    service, store = _service(settings, collector)

    first = service.prepare_for(today)
    store.save_playlist(first.playlist)
    store.save_seen(first.seen)

    # Day 5: standalone (4 days) is exhausted, books (5 days) is not.
    later = service.prepare_for(today + timedelta(days=4))

    assert later.rebuilt_pools == [PoolName.STANDALONE]
    assert collector.books_calls == 1
    assert collector.standalone_calls == 2
    assert len(later.bundle.quotes) == 3


def test_skipped_days_self_heal(settings, today, book_quotes, standalone_quotes):
    """A missed run leaves no drift - the next run reads its own date."""
    collector = StubCollector(book_quotes, standalone_quotes)
    service, store = _service(settings, collector)

    first = service.prepare_for(today)
    store.save_playlist(first.playlist)
    store.save_seen(first.seen)

    day_three = service.prepare_for(today + timedelta(days=2))

    assert day_three.rebuilt_pools == []
    assert len(day_three.bundle.quotes) == 3


def test_empty_pool_is_not_refetched_twice_on_the_same_day(settings, today, book_quotes):
    collector = StubCollector(book_quotes, [])
    service, store = _service(settings, collector)

    first = service.prepare_for(today)
    store.save_playlist(first.playlist)
    store.save_seen(first.seen)
    assert collector.standalone_calls == 1

    service.prepare_for(today)
    assert collector.standalone_calls == 1  # built_on guard held


def test_refresh_detects_new_quotes_on_an_existing_page(
    settings, today, book_quotes, standalone_quotes
):
    """Block-level identity catches quotes added to a page that already existed."""
    collector = StubCollector(book_quotes, standalone_quotes)
    service, _ = _service(settings, collector)

    prepared = service.prepare_for(today)
    collector.books = [*book_quotes, make_quote(500, PoolName.BOOKS)]

    result = service.refresh(prepared.playlist, prepared.seen, today)

    assert result.new_by_pool[PoolName.BOOKS] == 1
    assert result.changed


def test_refresh_ignores_quotes_it_has_already_seen(
    settings, today, book_quotes, standalone_quotes
):
    collector = StubCollector(book_quotes, standalone_quotes)
    service, _ = _service(settings, collector)
    prepared = service.prepare_for(today)

    result = service.refresh(prepared.playlist, prepared.seen, today)

    assert result.total_new == 0
    assert not result.changed


def test_refresh_appends_to_the_future_only(settings, today, book_quotes, standalone_quotes):
    collector = StubCollector(book_quotes, standalone_quotes)
    service, _ = _service(settings, collector)
    prepared = service.prepare_for(today)

    before = {
        day: [q.block_id for q in quotes]
        for day, quotes in prepared.playlist.get(PoolName.BOOKS).days.items()
    }
    collector.books = [*book_quotes, *(make_quote(i, PoolName.BOOKS) for i in range(600, 604))]

    service.refresh(prepared.playlist, prepared.seen, today)
    after = prepared.playlist.get(PoolName.BOOKS).days

    for day, ids in before.items():
        assert [q.block_id for q in after[day]][: len(ids)] == ids


def test_missed_refreshes_are_caught_up(settings, today, book_quotes, standalone_quotes):
    """The seen index is a record, not a cursor, so a skipped week is not lost."""
    collector = StubCollector(book_quotes, standalone_quotes)
    service, _ = _service(settings, collector)
    prepared = service.prepare_for(today)

    collector.books = [*book_quotes, make_quote(700, PoolName.BOOKS)]
    collector.standalone = [*standalone_quotes, make_quote(701, PoolName.STANDALONE, None)]

    # Three weeks later, first refresh since.
    result = service.refresh(prepared.playlist, prepared.seen, today + timedelta(days=21))

    assert result.new_by_pool[PoolName.BOOKS] == 1
    assert result.new_by_pool[PoolName.STANDALONE] == 1
