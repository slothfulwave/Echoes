"""Collector tests - callout extraction and attribution collapsing."""

from __future__ import annotations

from typing import Any

from echoes.collect.collector import QuoteCollector
from echoes.models import PoolName


class FakeAPI:
    """Serves canned Notion payloads."""

    def __init__(self, pages: list[dict[str, Any]], children: dict[str, list[dict[str, Any]]]):
        self._pages = pages
        self._children = children
        self.queries: list[dict[str, Any]] = []

    def query_database(self, database_id: str, *, filter_=None, page_size: int = 100):
        self.queries.append({"database_id": database_id, "filter": filter_})
        yield from self._pages

    def list_block_children(self, block_id: str, *, page_size: int = 100):
        yield from self._children.get(block_id, [])


def _page(page_id: str, title: str) -> dict[str, Any]:
    return {
        "id": page_id,
        "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}},
    }


def _callout(block_id: str, text: str, *, has_children: bool = False) -> dict[str, Any]:
    return {
        "id": block_id,
        "type": "callout",
        "has_children": has_children,
        "callout": {"rich_text": [{"plain_text": text}]},
    }


def _paragraph(block_id: str, text: str) -> dict[str, Any]:
    return {
        "id": block_id,
        "type": "paragraph",
        "has_children": False,
        "paragraph": {"rich_text": [{"plain_text": text}]},
    }


def test_each_callout_becomes_one_quote(settings):
    api = FakeAPI(
        pages=[_page("page-1", "Tuesdays With Morrie")],
        children={
            "page-1": [
                _callout("block-1", "Love wins. Love always wins."),
                _callout("block-2", "Love each other or perish."),
            ]
        },
    )
    quotes = QuoteCollector(api, settings.notion).collect_books()

    assert len(quotes) == 2
    assert quotes[0].block_id == "block-1"
    assert quotes[0].source_title == "Tuesdays With Morrie"
    assert quotes[0].pool is PoolName.BOOKS


def test_non_callout_blocks_are_ignored(settings):
    api = FakeAPI(
        pages=[_page("page-1", "A Book")],
        children={"page-1": [_paragraph("p-1", "Not a quote."), _callout("c-1", "A quote.")]},
    )
    quotes = QuoteCollector(api, settings.notion).collect_books()

    assert [q.block_id for q in quotes] == ["c-1"]


def test_attribution_collapses_onto_the_same_line(settings):
    api = FakeAPI(
        pages=[_page("quotes-page", "Quotes")],
        children={
            "quotes-page": [
                _callout("c-1", "If you are not busy being born, you are busy dying.", has_children=True)
            ],
            "c-1": [_paragraph("attr", "— Bob Dylan")],
        },
    )
    quotes = QuoteCollector(api, settings.notion, attribution_separator=" — ").collect_standalone()

    assert quotes[0].text == "If you are not busy being born, you are busy dying. — Bob Dylan"
    assert "\n" not in quotes[0].text


def test_existing_dash_is_not_doubled(settings):
    api = FakeAPI(
        pages=[_page("p", "Quotes")],
        children={"p": [_callout("c", "A quote.", has_children=True)], "c": [_paragraph("a", "- Someone")]},
    )
    quotes = QuoteCollector(api, settings.notion, attribution_separator=" — ").collect_standalone()

    assert quotes[0].text == "A quote. — Someone"


def test_standalone_quotes_carry_no_title(settings):
    api = FakeAPI(pages=[_page("p", "Quotes")], children={"p": [_callout("c", "A quote.")]})
    quotes = QuoteCollector(api, settings.notion).collect_standalone()

    assert quotes[0].source_title is None
    assert quotes[0].pool is PoolName.STANDALONE


def test_multiline_callout_text_is_flattened(settings):
    api = FakeAPI(
        pages=[_page("p", "A Book")],
        children={"p": [_callout("c", "Accept the past\nas past.")]},
    )
    quotes = QuoteCollector(api, settings.notion).collect_books()

    assert quotes[0].text == "Accept the past as past."


def test_empty_callouts_are_skipped(settings):
    api = FakeAPI(
        pages=[_page("p", "A Book")],
        children={"p": [_callout("c-1", "   "), _callout("c-2", "Real quote.")]},
    )
    quotes = QuoteCollector(api, settings.notion).collect_books()

    assert [q.block_id for q in quotes] == ["c-2"]


def test_a_book_with_no_quotes_contributes_nothing_and_does_not_raise(settings):
    api = FakeAPI(
        pages=[_page("empty-book", "Unquoted Book"), _page("full-book", "Quoted Book")],
        children={"empty-book": [], "full-book": [_callout("c", "A quote.")]},
    )
    quotes = QuoteCollector(api, settings.notion).collect_books()

    assert len(quotes) == 1
    assert quotes[0].source_title == "Quoted Book"


def test_books_filter_uses_status_and_date(settings):
    api = FakeAPI(pages=[], children={})
    QuoteCollector(api, settings.notion).collect_books()

    conditions = api.queries[0]["filter"]["and"]
    assert conditions[0] == {"property": "Status", "status": {"equals": "Completed"}}
    assert conditions[1] == {
        "property": "Completion Date",
        "date": {"on_or_after": "2024-04-24"},
    }


def test_standalone_filter_uses_the_tag(settings):
    api = FakeAPI(pages=[], children={})
    QuoteCollector(api, settings.notion).collect_standalone()

    assert api.queries[0]["filter"] == {
        "property": "Tags",
        "multi_select": {"contains": "Quote"},
    }
