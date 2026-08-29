"""Sender selection."""

from __future__ import annotations

from echoes.config import Settings
from echoes.deliver.base import Sender
from echoes.deliver.console import ConsoleSender
from echoes.deliver.twilio import TwilioSender
from echoes.logging_setup import get_logger

logger = get_logger(__name__)


def build_sender(settings: Settings) -> Sender:
    """Build the configured sender.

    ``expected_parameters`` is the fallback rate, because that is the largest
    number of lines a message can ever contain - the template must be
    registered with that many variables.
    """
    if settings.delivery_mode == "whatsapp":
        logger.info("Delivery mode: WhatsApp via Twilio")
        return TwilioSender(
            settings.twilio,
            expected_parameters=settings.quotes_per_day_books_fallback,
            book_separator=settings.book_separator,
            dry_run=settings.dry_run,
        )

    logger.info("Delivery mode: console (nothing is sent to WhatsApp)")
    return ConsoleSender(book_separator=settings.book_separator)
