"""Shared test fixtures."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from echoes.config import NotionSettings, Settings, TwilioSettings
from echoes.models import PoolName, Quote


@pytest.fixture
def today() -> date:
    return date(2026, 8, 10)  # A Monday.


@pytest.fixture
def sunday() -> date:
    return date(2026, 8, 16)


def make_quote(index: int, pool: PoolName = PoolName.BOOKS, title: str | None = "A Book") -> Quote:
    return Quote(
        block_id=f"{pool}-block-{index:04d}",
        text=f"Quote number {index}.",
        pool=pool,
        source_title=title if pool is PoolName.BOOKS else None,
    )


@pytest.fixture
def book_quotes() -> list[Quote]:
    return [make_quote(i, PoolName.BOOKS) for i in range(1, 11)]


@pytest.fixture
def standalone_quotes() -> list[Quote]:
    return [make_quote(i, PoolName.STANDALONE, None) for i in range(1, 5)]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        source="test",
        log_level="DEBUG",
        timezone=ZoneInfo("Asia/Kolkata"),
        timezone_name="Asia/Kolkata",
        state_dir=tmp_path / "state",
        dry_run=False,
        random_seed=42,
        quotes_per_day_books=2,
        quotes_per_day_standalone=1,
        quotes_per_day_books_fallback=3,
        book_separator=" - ",
        attribution_separator=" — ",
        delivery_mode="console",
        alerts_enabled=True,
        sunday_refresh_enabled=True,
        refresh_weekday=6,
        notion=NotionSettings(
            api_key="test-key",
            api_version="2022-06-28",
            timeout_seconds=5,
            max_retries=1,
            books_database_id="books-db",
            books_status_property="Status",
            books_status_value="Completed",
            books_date_property="Completion Date",
            books_completed_on_or_after=date(2024, 4, 24),
            me_section_database_id="me-db",
            me_section_tag_property="Tags",
            me_section_tag_value="Quote",
        ),
        twilio=TwilioSettings(
            account_sid="ACtest",
            auth_token="token",
            from_number="14155238886",
            recipients=["919999999999"],
            daily_content_sid="HXdaily",
            alert_content_sid="HXalert",
            timeout_seconds=5,
            max_retries=1,
        ),
    )


class StubCollector:
    """Stands in for QuoteCollector without touching Notion."""

    def __init__(self, books: list[Quote] | None = None, standalone: list[Quote] | None = None):
        self.books = books or []
        self.standalone = standalone or []
        self.books_calls = 0
        self.standalone_calls = 0

    def collect_books(self) -> list[Quote]:
        self.books_calls += 1
        return list(self.books)

    def collect_standalone(self) -> list[Quote]:
        self.standalone_calls += 1
        return list(self.standalone)
