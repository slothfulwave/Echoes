"""Domain models.

These are the only shapes that cross module boundaries. Everything persisted
to disk round-trips through ``to_dict`` / ``from_dict`` here, so the on-disk
JSON schema is defined in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Self

# Bumped only when the on-disk JSON layout changes incompatibly.
STATE_SCHEMA_VERSION = 1

ISO_DATE = "%Y-%m-%d"


class PoolName(StrEnum):
    """The two independent quote pools."""

    BOOKS = "books"
    STANDALONE = "standalone"


@dataclass(frozen=True, slots=True)
class Quote:
    """A single quote, identified by its Notion block UUID.

    ``block_id`` is stable across page edits and reordering, which is what
    makes new-quote detection reliable. ``source_title`` is the book title for
    the books pool and ``None`` for standalone quotes (which carry their
    attribution inline in ``text`` instead).
    """

    block_id: str
    text: str
    pool: PoolName
    source_title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "text": self.text,
            "pool": str(self.pool),
            "source_title": self.source_title,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        return cls(
            block_id=raw["block_id"],
            text=raw["text"],
            pool=PoolName(raw["pool"]),
            source_title=raw.get("source_title"),
        )


@dataclass(slots=True)
class PoolSchedule:
    """A calendar-keyed schedule for one pool.

    ``days`` maps ISO date strings to the quotes assigned to that date. Dates
    are assigned consecutively at build time, so there are never gaps between
    the first and last key - a skipped run simply means that date's quotes are
    never delivered, and tomorrow reads tomorrow's own key.

    ``per_day`` is recorded so the daily run can detect that the correct rate
    has changed (standalone pool emptied or refilled) and rebuild.
    """

    per_day: int
    cycle: int = 1
    built_on: str | None = None
    days: dict[str, list[Quote]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.days

    @property
    def first_date(self) -> date | None:
        return date.fromisoformat(min(self.days)) if self.days else None

    @property
    def last_date(self) -> date | None:
        return date.fromisoformat(max(self.days)) if self.days else None

    @property
    def total_quotes(self) -> int:
        return sum(len(quotes) for quotes in self.days.values())

    def quotes_for(self, day: date) -> list[Quote]:
        return list(self.days.get(day.strftime(ISO_DATE), []))

    def to_dict(self) -> dict[str, Any]:
        return {
            "per_day": self.per_day,
            "cycle": self.cycle,
            "built_on": self.built_on,
            "days": {
                day: [quote.to_dict() for quote in quotes]
                for day, quotes in sorted(self.days.items())
            },
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        return cls(
            per_day=int(raw["per_day"]),
            cycle=int(raw.get("cycle", 1)),
            built_on=raw.get("built_on"),
            days={
                day: [Quote.from_dict(q) for q in quotes]
                for day, quotes in raw.get("days", {}).items()
            },
        )


@dataclass(slots=True)
class Playlist:
    """The full prepared playlist - one schedule per pool."""

    pools: dict[PoolName, PoolSchedule] = field(default_factory=dict)
    version: int = STATE_SCHEMA_VERSION

    def get(self, pool: PoolName) -> PoolSchedule | None:
        return self.pools.get(pool)

    def set(self, pool: PoolName, schedule: PoolSchedule) -> None:
        self.pools[pool] = schedule

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "pools": {str(name): schedule.to_dict() for name, schedule in self.pools.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        return cls(
            version=int(raw.get("version", STATE_SCHEMA_VERSION)),
            pools={
                PoolName(name): PoolSchedule.from_dict(schedule)
                for name, schedule in raw.get("pools", {}).items()
            },
        )


@dataclass(slots=True)
class SeenIndex:
    """Every quote block UUID Echoes has ever scheduled, per pool.

    This is a complete record rather than a moving cursor, which is what makes
    the Sunday refresh self-healing: a missed or failed refresh is caught up
    automatically on the next run, with no reliance on a timestamp.
    """

    pools: dict[PoolName, set[str]] = field(default_factory=dict)
    version: int = STATE_SCHEMA_VERSION

    def ids(self, pool: PoolName) -> set[str]:
        return self.pools.setdefault(pool, set())

    def mark_seen(self, pool: PoolName, block_ids: set[str] | list[str]) -> int:
        """Add block ids to the pool. Returns how many were genuinely new."""
        known = self.ids(pool)
        incoming = set(block_ids)
        new = incoming - known
        known.update(incoming)
        return len(new)

    def unseen(self, pool: PoolName, quotes: list[Quote]) -> list[Quote]:
        known = self.ids(pool)
        return [quote for quote in quotes if quote.block_id not in known]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "pools": {str(name): sorted(ids) for name, ids in self.pools.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        return cls(
            version=int(raw.get("version", STATE_SCHEMA_VERSION)),
            pools={PoolName(name): set(ids) for name, ids in raw.get("pools", {}).items()},
        )


@dataclass(slots=True)
class DailyBundle:
    """What actually gets delivered on a given day."""

    day: date
    quotes: list[Quote]
    used_fallback: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.quotes
