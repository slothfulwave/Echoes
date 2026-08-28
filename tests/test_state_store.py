"""State persistence tests."""

from __future__ import annotations

import json

import pytest

from echoes.errors import StateError
from echoes.models import Playlist, PoolName, SeenIndex
from echoes.playlist.scheduler import build_schedule, make_rng
from echoes.playlist.state_store import StateStore


def test_missing_files_produce_empty_state(settings):
    store = StateStore(settings.state_dir)

    assert store.load_playlist().pools == {}
    assert store.load_seen().pools == {}


def test_playlist_round_trips(settings, today, book_quotes):
    store = StateStore(settings.state_dir)
    playlist = Playlist()
    playlist.set(
        PoolName.BOOKS,
        build_schedule(book_quotes, per_day=2, start_date=today, cycle=1, rng=make_rng(1)),
    )

    store.save_playlist(playlist)
    reloaded = store.load_playlist()

    assert reloaded.to_dict() == playlist.to_dict()
    assert reloaded.get(PoolName.BOOKS).total_quotes == 10


def test_seen_index_round_trips(settings):
    store = StateStore(settings.state_dir)
    seen = SeenIndex()
    seen.mark_seen(PoolName.BOOKS, {"a", "b"})
    seen.mark_seen(PoolName.STANDALONE, {"c"})

    store.save_seen(seen)
    reloaded = store.load_seen()

    assert reloaded.ids(PoolName.BOOKS) == {"a", "b"}
    assert reloaded.ids(PoolName.STANDALONE) == {"c"}


def test_mark_seen_reports_only_genuinely_new_ids(settings):
    seen = SeenIndex()

    assert seen.mark_seen(PoolName.BOOKS, {"a", "b"}) == 2
    assert seen.mark_seen(PoolName.BOOKS, {"b", "c"}) == 1


def test_malformed_json_raises_state_error(settings):
    store = StateStore(settings.state_dir)
    store.ensure_dir()
    store.playlist_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(StateError):
        store.load_playlist()


def test_written_state_is_human_readable(settings, today, book_quotes):
    """Quotes are stored inline so the file can be opened and read directly."""
    store = StateStore(settings.state_dir)
    playlist = Playlist()
    playlist.set(
        PoolName.BOOKS,
        build_schedule(book_quotes, per_day=2, start_date=today, cycle=1, rng=make_rng(1)),
    )
    store.save_playlist(playlist)

    raw = json.loads(store.playlist_path.read_text(encoding="utf-8"))
    first_day = next(iter(raw["pools"]["books"]["days"].values()))

    assert "text" in first_day[0]
    assert "\n" in store.playlist_path.read_text(encoding="utf-8")  # indented, not minified
