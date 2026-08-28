"""Proves the playlist can be serialised to a per-date quotes JSON file.

Runs through StubCollector, like test_service.py, so it never touches Notion
and never writes outside pytest's tmp_path.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from echoes.playlist import PlaylistService, StateStore
from tests.conftest import StubCollector


def _snapshot(service: PlaylistService, playlist, start: date, end: date) -> dict:
    """Quotes scheduled per date, ``start`` through ``end`` inclusive."""
    days: dict[str, list[dict]] = {}
    day = start
    while day <= end:
        bundle = service.bundle_for(playlist, day)
        days[day.isoformat()] = [quote.to_dict() for quote in bundle.quotes]
        day += timedelta(days=1)
    return {"range": {"start": start.isoformat(), "end": end.isoformat()}, "days": days}


def test_snapshot_file_is_created_for_the_requested_dates(
    settings, today, book_quotes, standalone_quotes, tmp_path
):
    store = StateStore(settings.state_dir)
    service = PlaylistService(store, StubCollector(book_quotes, standalone_quotes), settings)
    result = service.prepare_for(today)

    end = today + timedelta(days=2)
    snapshot = _snapshot(service, result.playlist, today, end)

    output_path = tmp_path / "quotes_snapshot.json"
    output_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    assert output_path.exists()

    written = json.loads(output_path.read_text())
    expected_dates = [(today + timedelta(days=i)).isoformat() for i in range(3)]
    assert list(written["days"].keys()) == expected_dates
    assert len(written["days"][today.isoformat()]) == 3
