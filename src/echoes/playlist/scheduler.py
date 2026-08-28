"""Pure scheduling logic.

No I/O, no Notion, no clock reads beyond what is passed in - which makes all
of it directly testable. Two operations exist:

``build_schedule``   assigns a freshly shuffled pool across consecutive dates.
``append_schedule``  adds newly found quotes without disturbing what is
                     already scheduled (the Sunday refresh).

Randomness happens here, at build and append time - never at delivery time.
Once a date is assigned, it is fixed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from echoes.logging_setup import get_logger
from echoes.models import ISO_DATE, PoolSchedule, Quote

logger = get_logger(__name__)


def make_rng(seed: int | None = None) -> random.Random:
    """Create the RNG. A fixed seed makes a run fully reproducible for tests."""
    return random.Random(seed)


def chunk(items: list[Quote], size: int) -> list[list[Quote]]:
    """Split into fixed-size bundles; the final bundle may be short.

    A short tail is intentional - the last day of a cycle simply delivers
    fewer quotes rather than borrowing from the next cycle.
    """
    if size < 1:
        raise ValueError("Bundle size must be at least 1.")
    return [items[i : i + size] for i in range(0, len(items), size)]


def build_schedule(
    quotes: list[Quote],
    *,
    per_day: int,
    start_date: date,
    cycle: int,
    rng: random.Random,
) -> PoolSchedule:
    """Shuffle ``quotes`` and assign them to consecutive dates from ``start_date``.

    The pool's horizon is its own size: ``ceil(len(quotes) / per_day)`` days.
    Pools are independent, so each one exhausts and rebuilds on its own clock.
    """
    shuffled = list(quotes)
    rng.shuffle(shuffled)

    bundles = chunk(shuffled, per_day)
    days: dict[str, list[Quote]] = {}
    for offset, bundle in enumerate(bundles):
        day = start_date + timedelta(days=offset)
        days[day.strftime(ISO_DATE)] = bundle

    schedule = PoolSchedule(
        per_day=per_day,
        cycle=cycle,
        built_on=start_date.strftime(ISO_DATE),
        days=days,
    )

    if bundles and len(bundles[-1]) < per_day:
        logger.info(
            "Final day of this cycle holds %d quote(s) instead of %d - the pool does not "
            "divide evenly, so that day's message will be shorter.",
            len(bundles[-1]), per_day,
        )

    return schedule


@dataclass(slots=True)
class AppendResult:
    """What the Sunday refresh actually changed."""

    topped_up: int = 0
    appended_quotes: int = 0
    appended_days: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.topped_up or self.appended_quotes)


def append_schedule(
    schedule: PoolSchedule,
    new_quotes: list[Quote],
    *,
    rng: random.Random,
    today: date,
) -> AppendResult:
    """Append newly found quotes to the future of an existing schedule.

    Append-only, in two steps:

    1. If the final day of the current cycle is a short tail *and* still in
       the future, top it up to full size first. This only ever adds quotes to
       a day - it never reorders or replaces what is already there - and it
       stops an uneven division from leaving a permanently short day.
    2. Assign whatever remains to new consecutive dates after the current end.

    Days at or before ``today`` are never touched: today's message has already
    gone out, and the past is immutable.
    """
    result = AppendResult()
    if not new_quotes:
        return result

    pending = list(new_quotes)
    rng.shuffle(pending)

    last = schedule.last_date

    if last is not None and last > today:
        key = last.strftime(ISO_DATE)
        shortfall = schedule.per_day - len(schedule.days[key])
        if shortfall > 0:
            fill, pending = pending[:shortfall], pending[shortfall:]
            schedule.days[key].extend(fill)
            result.topped_up = len(fill)
            logger.info(
                "Topped up the short tail on %s with %d quote(s) (now %d of %d)",
                key, len(fill), len(schedule.days[key]), schedule.per_day,
            )

    if not pending:
        return result

    # Never schedule into today or the past - today has already been delivered.
    start = max(last + timedelta(days=1), today + timedelta(days=1)) if last else today + timedelta(days=1)

    for offset, bundle in enumerate(chunk(pending, schedule.per_day)):
        day = start + timedelta(days=offset)
        schedule.days[day.strftime(ISO_DATE)] = bundle
        result.appended_days += 1

    result.appended_quotes = len(pending)
    logger.info(
        "Appended %d quote(s) across %d new day(s) starting %s",
        result.appended_quotes, result.appended_days, start.strftime(ISO_DATE),
    )
    return result
