"""Sender selection."""

from __future__ import annotations

from echoes.config import Settings
from echoes.deliver.base import Sender
from echoes.deliver.console import ConsoleSender
from echoes.deliver.email_sender import EmailSender
from echoes.logging_setup import get_logger
from echoes.playlist.state_store import StateStore

logger = get_logger(__name__)


def build_sender(settings: Settings) -> Sender:
    """Build the configured sender."""
    if settings.delivery_mode == "email":
        logger.info("Delivery mode: Email")
        return EmailSender(
            settings.email,
            state_store=StateStore(settings.state_dir),
            book_separator=settings.book_separator,
            dry_run=settings.dry_run,
        )

    logger.info("Delivery mode: console (nothing is sent)")
    return ConsoleSender(book_separator=settings.book_separator)
