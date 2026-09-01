"""Email delivery via Gmail SMTP.

There is no approval process or provider account needed - just a Gmail
account and an App Password. The body wraps the quotes in a fixed greeting
and sign-off (see ``GREETING``/``INTRO``/``SIGNOFF`` below), with a blank
line between each quote - a warmer, more spaced-out shape than the tighter
single-line-per-quote format ConsoleSender prints (that tighter shape is the
documented "agreed" message format, so this styling is deliberately kept
local to this sender rather than changed globally).

The one piece of real logic is thread continuity. The daily digest is meant
to land in a single, ongoing Gmail thread rather than a new conversation each
day. Gmail threads primarily by the ``In-Reply-To``/``References`` headers
(with an unchanged Subject as a secondary signal), so every email after the
first sets those headers to point back at every Message-ID sent before it.
Each day's run is a fresh process, so that history has to be persisted
between runs - see ``EmailThread`` in ``models.py`` and
``StateStore.load_email_thread``/``save_email_thread``.

Alerts are sent as their own separate, non-threaded email - kept out of the
daily digest thread deliberately, so a failure notice is easy to spot rather
than buried in an ongoing conversation.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import make_msgid

from echoes.config import EmailSettings
from echoes.deliver.base import Sender
from echoes.deliver.formatter import format_lines
from echoes.errors import DeliveryError
from echoes.logging_setup import get_logger, mask
from echoes.models import DailyBundle, EmailThread
from echoes.playlist.state_store import StateStore

logger = get_logger(__name__)

# The email's fixed greeting/sign-off - deliberately warmer than the plain
# numbered-list shape ConsoleSender/the "agreed" format uses, since this is
# meant to read as a note from Echoes rather than a raw digest dump.
GREETING = "Heyy! ❤️"
INTRO = "Sending you the quotes for today:"
SIGNOFF = "Have a lovely lovely day champ! 🌻"


def _build_body(bundle: DailyBundle, *, book_separator: str) -> str:
    """The full email body: greeting, quotes with a blank line between each, sign-off."""
    quotes = "\n\n".join(format_lines(bundle, book_separator=book_separator))
    return f"{GREETING}\n\n{INTRO}\n{quotes}\n\n{SIGNOFF}"


class EmailSender(Sender):
    """Sends the daily message as an email, kept in one ongoing Gmail thread."""

    def __init__(
        self,
        settings: EmailSettings,
        *,
        state_store: StateStore,
        book_separator: str = " - ",
        dry_run: bool = False,
    ) -> None:
        self._settings = settings
        self._store = state_store
        self._book_separator = book_separator
        self._dry_run = dry_run

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def send_daily(self, bundle: DailyBundle) -> None:
        if bundle.is_empty:
            raise DeliveryError("Refusing to send an empty message - no quotes were scheduled.")

        body = _build_body(bundle, book_separator=self._book_separator)
        thread = self._store.load_email_thread()
        message_id = make_msgid()
        msg = self._build_message(
            from_address=str(self._settings.from_address),
            to=str(self._settings.to_address),
            subject=self._settings.subject,
            body=body,
            message_id=message_id,
            thread=thread,
        )

        if self._dry_run:
            logger.info(
                "DRY RUN - would send email to %s (thread of %d prior message(s)):\n%s",
                mask(self._settings.to_address), len(thread.message_ids), body,
            )
            return

        self._deliver(msg)
        logger.info(
            "Delivered %d quote(s) for %s to %s (message id %s)",
            len(bundle.quotes), bundle.day, mask(self._settings.to_address), message_id,
        )

        thread.message_ids.append(message_id)
        self._store.save_email_thread(thread)

    def send_alert(self, message: str) -> None:
        """Best-effort alert. Never raises - an alert failure must not mask the
        original failure it was trying to report. Sent as its own separate
        email, not threaded into the daily digest."""
        msg = EmailMessage()
        msg["From"] = self._settings.from_address
        msg["To"] = self._settings.to_address
        msg["Subject"] = f"{self._settings.subject} — Alert"
        msg["Message-ID"] = make_msgid()
        msg.set_content(message)

        if self._dry_run:
            logger.info("DRY RUN - would send alert email: %s", message)
            return

        try:
            self._deliver(msg)
            logger.info("Alert delivered: %s", message)
        except Exception as exc:  # noqa: BLE001 - alerting must never raise
            logger.error("Could not deliver alert email (%s). Alert text was: %s", exc, message)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _build_message(
        *, from_address: str, to: str, subject: str, body: str, message_id: str, thread: EmailThread
    ) -> EmailMessage:
        """Pure construction, no I/O - what makes the threading logic testable."""
        msg = EmailMessage()
        msg["From"] = from_address
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = message_id
        if thread.message_ids:
            # In-Reply-To names the immediate parent; References chains the
            # full ancestor history, oldest first - both are how Gmail knows
            # this belongs in the same thread as every prior send.
            msg["In-Reply-To"] = thread.message_ids[-1]
            msg["References"] = " ".join(thread.message_ids)
        msg.set_content(body)
        return msg

    def _deliver(self, msg: EmailMessage) -> None:
        try:
            with smtplib.SMTP(
                str(self._settings.smtp_host),
                self._settings.smtp_port,
                timeout=self._settings.timeout_seconds,
            ) as smtp:
                smtp.starttls()
                smtp.login(str(self._settings.from_address), str(self._settings.app_password))
                smtp.send_message(msg)
        except smtplib.SMTPException as exc:
            raise DeliveryError(f"SMTP error sending email: {exc}") from exc
        except OSError as exc:
            raise DeliveryError(f"Network error sending email: {exc}") from exc
