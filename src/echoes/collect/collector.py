"""Turns Notion pages into :class:`Quote` objects.

Both sources follow the same shape - a filtered database query yields pages,
and every callout block in a page body is one quote:

* **Books pool**  - ``Books! Books! Books!`` where Status = Completed and
  Completion Date >= the configured cutoff. Each quote carries its book title.
* **Standalone pool** - ``The Me Section`` where Tags contains "Quote". These
  pages carry no title on the quote; any attribution written inside the
  callout is folded into the quote text instead.

Adding a second standalone quotes page needs no code change: tag it "Quote"
in Notion and the next refresh picks it up.
"""

from __future__ import annotations

import re
from typing import Any

from echoes.collect.notion_api import NotionAPI
from echoes.config import NotionSettings
from echoes.errors import NotionAPIError
from echoes.logging_setup import get_logger
from echoes.models import PoolName, Quote

logger = get_logger(__name__)

CALLOUT = "callout"

# Leading dash characters used for attribution lines in Notion, e.g. "— Bob Dylan".
_LEADING_DASHES = re.compile(r"^[\s\u2014\u2013\u2015\-]+")


def _normalise(text: str) -> str:
    """Collapse all whitespace, including newlines, into single spaces.

    Multi-line callouts are flattened to one line at collection time, so every
    quote renders as a single clean entry in the numbered list wherever it's
    delivered, rather than breaking the numbering with an embedded line break.
    """
    return " ".join(text.split())


def _rich_text_to_plain(rich_text: list[dict[str, Any]] | None) -> str:
    if not rich_text:
        return ""
    return "".join(fragment.get("plain_text", "") for fragment in rich_text)


def _block_plain_text(block: dict[str, Any]) -> str:
    """Extract text from any block type that carries ``rich_text``."""
    block_type = block.get("type", "")
    body = block.get(block_type)
    if not isinstance(body, dict):
        return ""
    return _rich_text_to_plain(body.get("rich_text"))


class QuoteCollector:
    """Reads both quote pools from Notion."""

    def __init__(self, api: NotionAPI, settings: NotionSettings, *, attribution_separator: str = " — ") -> None:
        self._api = api
        self._settings = settings
        self._attribution_separator = attribution_separator

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def collect_books(self) -> list[Quote]:
        """Collect quotes from completed books published on or after the cutoff."""
        cutoff = self._settings.books_completed_on_or_after.isoformat()
        logger.info(
            "Collecting books pool: %s = %r AND %s >= %s",
            self._settings.books_status_property,
            self._settings.books_status_value,
            self._settings.books_date_property,
            cutoff,
        )

        pages = self._query_books_database()
        logger.info("Books pool: %d book page(s) matched the filter", len(pages))

        quotes: list[Quote] = []
        empty_books: list[str] = []

        for page in pages:
            title = self._page_title(page) or "Untitled"
            page_quotes = self._extract_quotes(
                page_id=page["id"], pool=PoolName.BOOKS, source_title=title
            )
            if not page_quotes:
                # A completed book with no quotes yet is normal, not an error.
                empty_books.append(title)
                logger.debug("Book %r has no quotes yet - skipping silently", title)
            quotes.extend(page_quotes)

        logger.info(
            "Books pool: collected %d quote(s) from %d book(s) (%d book(s) had none)",
            len(quotes), len(pages) - len(empty_books), len(empty_books),
        )
        return quotes

    def collect_standalone(self) -> list[Quote]:
        """Collect quotes from every page in The Me Section tagged "Quote"."""
        logger.info(
            "Collecting standalone pool: %s contains %r",
            self._settings.me_section_tag_property,
            self._settings.me_section_tag_value,
        )

        pages = self._query_me_section_database()
        logger.info("Standalone pool: %d tagged page(s) matched the filter", len(pages))

        quotes: list[Quote] = []
        for page in pages:
            title = self._page_title(page) or "Untitled"
            page_quotes = self._extract_quotes(
                page_id=page["id"], pool=PoolName.STANDALONE, source_title=None
            )
            logger.debug("Standalone page %r contributed %d quote(s)", title, len(page_quotes))
            quotes.extend(page_quotes)

        logger.info("Standalone pool: collected %d quote(s)", len(quotes))
        return quotes

    # ------------------------------------------------------------------
    # Database queries
    # ------------------------------------------------------------------
    def _query_books_database(self) -> list[dict[str, Any]]:
        """Query the books database.

        Notion distinguishes ``status`` and ``select`` property types and
        rejects the wrong filter key with HTTP 400. The board column in the
        screenshot is a Status property, so that is tried first, with a
        transparent fallback to ``select`` so a differently-typed database
        does not require a code change.
        """
        date_filter = {
            "property": self._settings.books_date_property,
            "date": {"on_or_after": self._settings.books_completed_on_or_after.isoformat()},
        }

        for property_type in ("status", "select"):
            filter_ = {
                "and": [
                    {
                        "property": self._settings.books_status_property,
                        property_type: {"equals": self._settings.books_status_value},
                    },
                    date_filter,
                ]
            }
            try:
                return list(self._api.query_database(self._settings.books_database_id, filter_=filter_))
            except NotionAPIError as exc:
                if property_type == "status" and exc.status_code == 400:
                    logger.warning(
                        "Status filter rejected (%s); retrying as a 'select' property. "
                        "If this succeeds, the retry is harmless but you may want to confirm "
                        "the property type in Notion.",
                        exc.code or "validation_error",
                    )
                    continue
                raise

        return []  # pragma: no cover - unreachable; loop either returns or raises

    def _query_me_section_database(self) -> list[dict[str, Any]]:
        """Query The Me Section, tolerating multi_select vs select tag types."""
        for property_type in ("multi_select", "select"):
            condition = (
                {"contains": self._settings.me_section_tag_value}
                if property_type == "multi_select"
                else {"equals": self._settings.me_section_tag_value}
            )
            filter_ = {"property": self._settings.me_section_tag_property, property_type: condition}
            try:
                return list(
                    self._api.query_database(self._settings.me_section_database_id, filter_=filter_)
                )
            except NotionAPIError as exc:
                if property_type == "multi_select" and exc.status_code == 400:
                    logger.warning(
                        "Multi-select tag filter rejected (%s); retrying as a 'select' property.",
                        exc.code or "validation_error",
                    )
                    continue
                raise

        return []  # pragma: no cover

    # ------------------------------------------------------------------
    # Block extraction
    # ------------------------------------------------------------------
    def _extract_quotes(self, *, page_id: str, pool: PoolName, source_title: str | None) -> list[Quote]:
        """Every callout block in the page body becomes one quote.

        The block UUID is the quote's identity - stable across edits to the
        surrounding page and across reordering, which is what the Sunday
        refresh diff relies on.
        """
        quotes: list[Quote] = []

        for block in self._api.list_block_children(page_id):
            if block.get("type") != CALLOUT:
                continue

            text = _normalise(_rich_text_to_plain(block.get(CALLOUT, {}).get("rich_text")))
            if not text:
                logger.debug("Skipping empty callout %s on page %s", block.get("id"), page_id)
                continue

            if block.get("has_children"):
                attribution = self._read_attribution(block["id"])
                if attribution:
                    text = self._join_attribution(text, attribution)

            quotes.append(
                Quote(block_id=block["id"], text=text, pool=pool, source_title=source_title)
            )

        return quotes

    def _read_attribution(self, block_id: str) -> str:
        """Read nested lines inside a callout (e.g. the author credit)."""
        parts = [
            _normalise(_block_plain_text(child))
            for child in self._api.list_block_children(block_id)
        ]
        return " ".join(part for part in parts if part).strip()

    def _join_attribution(self, text: str, attribution: str) -> str:
        """Collapse the attribution onto the same line as the quote.

        Notion renders it on its own line, but a single line reads better in
        the message, and keeps the quote to one clean entry in the numbered list.
        Any dash the author already typed is stripped so the separator is
        applied exactly once.
        """
        cleaned = _LEADING_DASHES.sub("", attribution).strip()
        if not cleaned:
            return text
        return f"{text.rstrip()}{self._attribution_separator}{cleaned}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _page_title(page: dict[str, Any]) -> str | None:
        """Read a page's title property, whatever it happens to be named."""
        for prop in page.get("properties", {}).values():
            if prop.get("type") == "title":
                title = _normalise(_rich_text_to_plain(prop.get("title")))
                return title or None
        return None
