"""Message formatting tests - the agreed line shape."""

from __future__ import annotations

from echoes.deliver.formatter import format_bundle, format_lines, format_quote
from echoes.models import DailyBundle, PoolName, Quote


def _bundle(today, quotes: list[Quote]) -> DailyBundle:
    return DailyBundle(day=today, quotes=quotes)


def test_book_quote_carries_its_title():
    quote = Quote("b1", "Love wins.", PoolName.BOOKS, "Tuesdays With Morrie")
    assert format_quote(quote) == "Love wins. - Tuesdays With Morrie"


def test_standalone_quote_has_no_title():
    quote = Quote("s1", "History doesn't repeat itself, but it rhymes.", PoolName.STANDALONE, None)
    assert format_quote(quote) == "History doesn't repeat itself, but it rhymes."


def test_standard_day_is_two_books_then_one_standalone(today):
    bundle = _bundle(
        today,
        [
            Quote("b1", "First.", PoolName.BOOKS, "Book One"),
            Quote("b2", "Second.", PoolName.BOOKS, "Book Two"),
            Quote("s1", "Third.", PoolName.STANDALONE, None),
        ],
    )
    assert format_bundle(bundle) == "1. First. - Book One\n2. Second. - Book Two\n3. Third."


def test_fallback_day_labels_all_three_lines(today):
    """Standalone pool empty -> three book quotes, all carrying titles."""
    bundle = _bundle(
        today,
        [
            Quote("b1", "First.", PoolName.BOOKS, "Book One"),
            Quote("b2", "Second.", PoolName.BOOKS, "Book Two"),
            Quote("b3", "Third.", PoolName.BOOKS, "Book Three"),
        ],
    )
    assert format_bundle(bundle).splitlines()[2] == "3. Third. - Book Three"


def test_short_tail_day_renders_fewer_lines(today):
    bundle = _bundle(today, [Quote("b1", "Only one.", PoolName.BOOKS, "Book One")])
    assert format_lines(bundle) == ["1. Only one. - Book One"]


def test_newlines_are_flattened(today):
    """A multi-line callout renders as one clean line, not a broken numbered list."""
    bundle = _bundle(today, [Quote("b1", "Line one\nline two", PoolName.BOOKS, "Book")])
    assert "\n" not in format_lines(bundle)[0]
