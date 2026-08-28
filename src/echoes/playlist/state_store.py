"""Persistence for the two state files.

``state/quotes_schedule.json``  - the prepared playlist, one section per pool.
``state/seen_blocks.json``      - every quote block UUID ever scheduled.

Both are committed to the repository by the workflow, which is what makes the
playlist survive between runs on ephemeral runners. Quotes are stored inline
rather than by reference so the schedule file can be opened and read directly -
transparency over normalisation, per the project's design values.

Writes are atomic (temp file + ``os.replace``) so an interrupted run can never
leave a half-written playlist behind.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from echoes.errors import StateError
from echoes.logging_setup import get_logger
from echoes.models import Playlist, SeenIndex

logger = get_logger(__name__)

PLAYLIST_FILENAME = "quotes_schedule.json"
SEEN_FILENAME = "seen_blocks.json"


class StateStore:
    """Reads and writes Echoes state as JSON files."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = Path(state_dir)

    @property
    def playlist_path(self) -> Path:
        return self._state_dir / PLAYLIST_FILENAME

    @property
    def seen_path(self) -> Path:
        return self._state_dir / SEEN_FILENAME

    def ensure_dir(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Playlist
    # ------------------------------------------------------------------
    def load_playlist(self) -> Playlist:
        raw = self._read_json(self.playlist_path)
        if raw is None:
            logger.info("No playlist found at %s - one will be prepared", self.playlist_path)
            return Playlist()
        try:
            playlist = Playlist.from_dict(raw)
        except (KeyError, ValueError, TypeError) as exc:
            raise StateError(f"Playlist file {self.playlist_path} is malformed: {exc}") from exc

        summary = ", ".join(
            f"{name}={schedule.total_quotes} quote(s) across {len(schedule.days)} day(s)"
            for name, schedule in playlist.pools.items()
        )
        logger.info("Loaded playlist (%s)", summary or "empty")
        return playlist

    def save_playlist(self, playlist: Playlist) -> None:
        self._write_json(self.playlist_path, playlist.to_dict())
        logger.info("Playlist written to %s", self.playlist_path)

    # ------------------------------------------------------------------
    # Seen index
    # ------------------------------------------------------------------
    def load_seen(self) -> SeenIndex:
        raw = self._read_json(self.seen_path)
        if raw is None:
            logger.info("No seen index found at %s - starting a new one", self.seen_path)
            return SeenIndex()
        try:
            seen = SeenIndex.from_dict(raw)
        except (KeyError, ValueError, TypeError) as exc:
            raise StateError(f"Seen index file {self.seen_path} is malformed: {exc}") from exc

        summary = ", ".join(f"{name}={len(ids)}" for name, ids in seen.pools.items())
        logger.info("Loaded seen index (%s)", summary or "empty")
        return seen

    def save_seen(self, seen: SeenIndex) -> None:
        self._write_json(self.seen_path, seen.to_dict())
        logger.info("Seen index written to %s", self.seen_path)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _read_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except json.JSONDecodeError as exc:
            raise StateError(f"State file {path} contains invalid JSON: {exc}") from exc
        except OSError as exc:
            raise StateError(f"Could not read state file {path}: {exc}") from exc

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self.ensure_dir()
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, path)
        except OSError as exc:
            raise StateError(f"Could not write state file {path}: {exc}") from exc
