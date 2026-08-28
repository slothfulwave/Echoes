"""Scheduler tests - build, short tails, and append-only refresh."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from echoes.models import ISO_DATE, PoolName
from echoes.playlist.scheduler import append_schedule, build_schedule, chunk, make_rng
from tests.conftest import make_quote


def test_chunk_splits_evenly():
    quotes = [make_quote(i) for i in range(1, 7)]
    assert [len(bundle) for bundle in chunk(quotes, 2)] == [2, 2, 2]


def test_chunk_leaves_a_short_tail():
    quotes = [make_quote(i) for i in range(1, 6)]
    assert [len(bundle) for bundle in chunk(quotes, 2)] == [2, 2, 1]


def test_chunk_rejects_zero_size():
    with pytest.raises(ValueError):
        chunk([make_quote(1)], 0)


def test_build_assigns_consecutive_dates(today, book_quotes):
    schedule = build_schedule(
        book_quotes, per_day=2, start_date=today, cycle=1, rng=make_rng(1)
    )

    assert len(schedule.days) == 5  # 10 quotes / 2 per day
    expected = [(today + timedelta(days=i)).strftime(ISO_DATE) for i in range(5)]
    assert sorted(schedule.days) == expected
    assert schedule.first_date == today
    assert schedule.last_date == today + timedelta(days=4)


def test_build_horizon_differs_per_pool(today, book_quotes, standalone_quotes):
    """Independent pools do not share a horizon - this is the point of the design."""
    books = build_schedule(book_quotes, per_day=2, start_date=today, cycle=1, rng=make_rng(1))
    standalone = build_schedule(
        standalone_quotes, per_day=1, start_date=today, cycle=1, rng=make_rng(1)
    )

    assert len(books.days) == 5       # 10 quotes at 2/day
    assert len(standalone.days) == 4  # 4 quotes at 1/day


def test_build_keeps_every_quote_exactly_once(today, book_quotes):
    schedule = build_schedule(
        book_quotes, per_day=3, start_date=today, cycle=1, rng=make_rng(7)
    )
    scheduled = [q.block_id for bundle in schedule.days.values() for q in bundle]

    assert sorted(scheduled) == sorted(q.block_id for q in book_quotes)
    assert len(scheduled) == len(set(scheduled))


def test_build_tail_day_may_be_short(today):
    quotes = [make_quote(i) for i in range(1, 6)]  # 5 quotes at 2/day -> 2,2,1
    schedule = build_schedule(quotes, per_day=2, start_date=today, cycle=1, rng=make_rng(3))

    assert len(schedule.days[schedule.last_date.strftime(ISO_DATE)]) == 1
    assert schedule.total_quotes == 5


def test_build_with_no_quotes_is_empty(today):
    schedule = build_schedule([], per_day=2, start_date=today, cycle=1, rng=make_rng(1))

    assert schedule.is_empty
    assert schedule.built_on == today.strftime(ISO_DATE)
    assert schedule.last_date is None


def test_build_is_deterministic_for_a_fixed_seed(today, book_quotes):
    first = build_schedule(book_quotes, per_day=2, start_date=today, cycle=1, rng=make_rng(99))
    second = build_schedule(book_quotes, per_day=2, start_date=today, cycle=1, rng=make_rng(99))

    assert first.to_dict() == second.to_dict()


def test_append_tops_up_a_future_short_tail(today):
    quotes = [make_quote(i) for i in range(1, 6)]  # tail day holds 1
    schedule = build_schedule(quotes, per_day=2, start_date=today, cycle=1, rng=make_rng(3))
    tail_key = schedule.last_date.strftime(ISO_DATE)

    result = append_schedule(schedule, [make_quote(99)], rng=make_rng(1), today=today)

    assert result.topped_up == 1
    assert len(schedule.days[tail_key]) == 2
    assert result.appended_quotes == 0


def test_append_never_touches_today_or_the_past(today):
    """Today's message has already gone out, so today is immutable."""
    quotes = [make_quote(1)]  # single day: today, holding 1 of 2
    schedule = build_schedule(quotes, per_day=2, start_date=today, cycle=1, rng=make_rng(1))

    result = append_schedule(schedule, [make_quote(50)], rng=make_rng(1), today=today)

    assert result.topped_up == 0
    assert len(schedule.days[today.strftime(ISO_DATE)]) == 1
    assert schedule.days[(today + timedelta(days=1)).strftime(ISO_DATE)][0].block_id.endswith("0050")


def test_append_extends_after_the_current_end(today, book_quotes):
    schedule = build_schedule(book_quotes, per_day=2, start_date=today, cycle=1, rng=make_rng(1))
    original_end = schedule.last_date
    original_days = dict(schedule.days)

    new_quotes = [make_quote(i) for i in range(20, 24)]
    result = append_schedule(schedule, new_quotes, rng=make_rng(1), today=today)

    assert result.appended_quotes == 4
    assert result.appended_days == 2
    assert schedule.last_date == original_end + timedelta(days=2)
    # Existing days are untouched.
    for key, bundle in original_days.items():
        assert [q.block_id for q in schedule.days[key]] == [q.block_id for q in bundle]


def test_append_with_nothing_new_is_a_noop(today, book_quotes):
    schedule = build_schedule(book_quotes, per_day=2, start_date=today, cycle=1, rng=make_rng(1))
    before = schedule.to_dict()

    result = append_schedule(schedule, [], rng=make_rng(1), today=today)

    assert not result.changed
    assert schedule.to_dict() == before


def test_append_to_an_exhausted_schedule_starts_tomorrow(today):
    """If the cycle ended in the past, new quotes start from tomorrow, not from
    the stale end date."""
    past = today - timedelta(days=10)
    schedule = build_schedule(
        [make_quote(1), make_quote(2)], per_day=2, start_date=past, cycle=1, rng=make_rng(1)
    )

    append_schedule(schedule, [make_quote(60)], rng=make_rng(1), today=today)

    assert schedule.last_date == today + timedelta(days=1)


def test_quotes_for_returns_the_right_day(today, book_quotes):
    schedule = build_schedule(book_quotes, per_day=2, start_date=today, cycle=1, rng=make_rng(1))

    assert len(schedule.quotes_for(today)) == 2
    assert schedule.quotes_for(today - timedelta(days=1)) == []
