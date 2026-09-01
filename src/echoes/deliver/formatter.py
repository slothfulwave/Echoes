"""Message formatting.

The agreed shape is one numbered line per quote::

    1. <quote> - Book Name
    2. <quote> - Book Name
    3. <quote>

Book quotes carry their title. Standalone quotes do not - any attribution
they have was folded into the quote text at collection time, so line 3 is
usually bare. Under the fallback (standalone pool empty) all three lines are
book quotes and all three carry titles.
"""

from __future__ import annotations

from echoes.logging_setup import get_logger
from echoes.models import DailyBundle, Quote

logger = get_logger(__name__)


def format_quote(quote: Quote, *, book_separator: str = " - ") -> str:
    """Render a single quote without its line number."""
    text = " ".join(quote.text.split())
    if quote.source_title:
        return f"{text}{book_separator}{quote.source_title}"
    return text


def format_lines(bundle: DailyBundle, *, book_separator: str = " - ") -> list[str]:
    """Render the bundle as numbered lines, one per quote.

    Returned as a list rather than a single string so each sender can join
    them its own way - EmailSender puts a blank line between quotes, while
    ``format_bundle`` below uses this same list for the tighter, single-line
    "agreed" shape.
    """
    return [
        f"{index}. {format_quote(quote, book_separator=book_separator)}"
        for index, quote in enumerate(bundle.quotes, start=1)
    ]


def format_bundle(bundle: DailyBundle, *, book_separator: str = " - ") -> str:
    """Render the whole message as displayed text (console, logs, dry runs)."""
    return "\n".join(format_lines(bundle, book_separator=book_separator))
