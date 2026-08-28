"""Console delivery.

Prints the message that *would* be sent. This is what makes the whole system
runnable and testable before the WhatsApp Business account exists - collection,
scheduling, refresh and failure handling all work exactly as they will in
production, only the final hop changes.
"""

from __future__ import annotations

from echoes.deliver.base import Sender
from echoes.deliver.formatter import format_bundle
from echoes.logging_setup import get_logger
from echoes.models import DailyBundle

logger = get_logger(__name__)

_RULE = "-" * 68


class ConsoleSender(Sender):
    """Writes the daily message to the log instead of sending it."""

    def __init__(self, *, book_separator: str = " - ") -> None:
        self._book_separator = book_separator

    def send_daily(self, bundle: DailyBundle) -> None:
        body = format_bundle(bundle, book_separator=self._book_separator)
        logger.info(
            "DELIVERY (console mode) - %d quote(s) for %s\n%s\n%s\n%s",
            len(bundle.quotes), bundle.day, _RULE, body, _RULE,
        )

    def send_alert(self, message: str) -> None:
        logger.warning("ALERT (console mode): %s", message)
