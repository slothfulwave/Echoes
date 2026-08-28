"""End-to-end pipeline tests.

These drive ``run_daily`` exactly as the workflow does, substituting only the
Notion HTTP transport. Delivery runs through the real ConsoleSender.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from echoes.errors import NotionAPIError
from echoes.models import PoolName
from echoes.pipeline import daily as daily_module
from echoes.pipeline.daily import run_daily
from echoes.playlist import StateStore


class FakeNotionAPI:
    """Drop-in replacement for NotionAPI backed by canned payloads."""

    pages: dict[str, list[dict[str, Any]]] = {}
    children: dict[str, list[dict[str, Any]]] = {}
    fail: bool = False

    def __init__(self, *_args, **_kwargs):
        # Deliberately does no I/O - the real client only builds a session here,
        # so an outage must surface on the first request, not on construction.
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def close(self):
        return None

    @staticmethod
    def _raise():
        raise NotionAPIError("simulated Notion outage", status_code=503)

    def query_database(self, database_id: str, *, filter_=None, page_size: int = 100):
        if self.fail:
            self._raise()
        yield from self.pages.get(database_id, [])

    def list_block_children(self, block_id: str, *, page_size: int = 100):
        if self.fail:
            self._raise()
        yield from self.children.get(block_id, [])


def _page(page_id: str, title: str) -> dict[str, Any]:
    return {
        "id": page_id,
        "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}},
    }


def _callout(block_id: str, text: str) -> dict[str, Any]:
    return {
        "id": block_id,
        "type": "callout",
        "has_children": False,
        "callout": {"rich_text": [{"plain_text": text}]},
    }


def _freeze_date(monkeypatch, year: int, month: int, day: int) -> None:
    """Pin the pipeline's clock so date-dependent behaviour is testable."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    frozen = datetime(year, month, day, 7, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(daily_module, "datetime", FrozenDatetime)


@pytest.fixture
def fake_notion(monkeypatch, settings):
    """Wire a populated fake Notion into the daily pipeline."""
    FakeNotionAPI.fail = False
    FakeNotionAPI.pages = {
        "books-db": [_page("book-1", "Tuesdays With Morrie"), _page("book-2", "Siddhartha")],
        "me-db": [_page("quotes-page", "Quotes")],
    }
    FakeNotionAPI.children = {
        "book-1": [_callout(f"m-{i}", f"Morrie quote {i}.") for i in range(1, 5)],
        "book-2": [_callout(f"s-{i}", f"Siddhartha quote {i}.") for i in range(1, 5)],
        "quotes-page": [_callout(f"q-{i}", f"Standalone quote {i}.") for i in range(1, 4)],
    }
    monkeypatch.setattr(daily_module, "NotionAPI", FakeNotionAPI)
    return FakeNotionAPI


def test_full_run_delivers_three_quotes(settings, fake_notion, caplog):
    report = run_daily(settings)

    assert report.delivered
    assert report.quotes_sent == 3
    assert not report.degraded
    assert report.alerts == []
    assert report.succeeded


def test_full_run_writes_both_state_files(settings, fake_notion):
    run_daily(settings)
    store = StateStore(settings.state_dir)

    assert store.playlist_path.exists()
    assert store.seen_path.exists()

    playlist = store.load_playlist()
    assert playlist.get(PoolName.BOOKS).total_quotes == 8
    assert playlist.get(PoolName.STANDALONE).total_quotes == 3

    seen = store.load_seen()
    assert len(seen.ids(PoolName.BOOKS)) == 8
    assert len(seen.ids(PoolName.STANDALONE)) == 3


def test_message_has_the_agreed_shape(settings, fake_notion, caplog):
    with caplog.at_level("INFO"):
        run_daily(settings)

    delivery = next(r.getMessage() for r in caplog.records if "DELIVERY (console mode)" in r.getMessage())
    lines = [line for line in delivery.splitlines() if line[:2] in {"1.", "2.", "3."}]

    assert len(lines) == 3
    assert " - " in lines[0] and " - " in lines[1]  # book quotes carry titles
    assert " - " not in lines[2]  # standalone quote does not


def test_second_run_reuses_the_playlist(settings, fake_notion, caplog):
    run_daily(settings)
    before = StateStore(settings.state_dir).playlist_path.read_text(encoding="utf-8")

    with caplog.at_level("INFO"):
        caplog.clear()
        report = run_daily(settings)

    assert report.delivered
    assert "Rebuilding" not in caplog.text
    assert StateStore(settings.state_dir).playlist_path.read_text(encoding="utf-8") == before


def test_dry_run_writes_nothing(settings, fake_notion):
    report = run_daily(dataclasses.replace(settings, dry_run=True))

    assert report.delivered
    assert not StateStore(settings.state_dir).playlist_path.exists()


def test_notion_outage_during_a_rebuild_degrades_and_alerts(settings, fake_notion, monkeypatch):
    """An outage only matters when a rebuild is due - and even then it alerts
    rather than failing silently."""
    run_daily(settings)  # books cover 4 days, standalone 3

    fake_notion.fail = True
    _freeze_date(monkeypatch, 2026, 8, 20)  # well past both horizons
    report = run_daily(settings)

    assert report.degraded
    assert report.alerts  # failures are never silent
    assert "existing playlist" in report.alerts[0]


def test_outage_on_a_prepared_day_still_delivers(settings, fake_notion, today):
    """The playlist covers several days, so an outage does not stop delivery."""
    run_daily(settings)
    fake_notion.fail = True

    report = run_daily(settings)

    # The pools already cover today, so no Notion call is needed at all.
    assert report.delivered
    assert report.quotes_sent == 3


def test_outage_with_no_playlist_sends_nothing_but_alerts(settings, fake_notion):
    fake_notion.fail = True

    report = run_daily(settings)

    assert not report.delivered
    assert len(report.alerts) == 2  # preparation failed, and nothing was scheduled


def test_sunday_refresh_runs_after_delivery(settings, fake_notion, monkeypatch):
    """New quotes added between runs are appended on the refresh weekday."""
    run_daily(settings)

    # Add a quote to an existing book page - block-level identity catches this.
    fake_notion.children["book-1"].append(_callout("m-99", "A newly added Morrie quote."))

    _freeze_date(monkeypatch, 2026, 8, 9)  # a Sunday
    report = run_daily(settings)

    assert report.delivered
    assert report.refreshed

    seen = StateStore(settings.state_dir).load_seen()
    assert "m-99" in seen.ids(PoolName.BOOKS)
