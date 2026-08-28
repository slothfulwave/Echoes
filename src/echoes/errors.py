"""Exception hierarchy for Echoes.

The distinction that matters operationally:

* ``ConfigurationError`` is fatal - nothing can run, so we exit loudly.
* ``CollectionError`` / ``StateError`` are recoverable - the daily pipeline
  falls back to the existing playlist and raises an alert instead of dying.
* ``DeliveryError`` means the message did not reach the phone. There is no
  fallback for this one; it is reported and the run is marked failed.
"""

from __future__ import annotations


class EchoesError(Exception):
    """Base class for every error raised by Echoes."""


class ConfigurationError(EchoesError):
    """Missing or invalid configuration. Fatal - the run cannot proceed."""


class CollectionError(EchoesError):
    """Quotes could not be collected from Notion. Recoverable."""


class NotionAPIError(CollectionError):
    """The Notion API returned an error response."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class StateError(EchoesError):
    """Playlist or seen-index state could not be read or written."""


class DeliveryError(EchoesError):
    """The daily message could not be delivered."""
