"""WhatsApp delivery via Twilio's API.

Echoes sends WhatsApp messages through Twilio rather than calling Meta's
Graph API directly. Twilio still runs on top of the same underlying WhatsApp
Business Platform - a business-initiated message still requires a
pre-approved template - but Twilio handles the Meta business verification
and template approval on your behalf, and identifies templates by a Content
SID (``HXxxxxxxxx...``) rather than a template name.

Because there is still no inbound message and so no open 24-hour session
window, every message remains business-initiated and needs its own approved
Content Template - the daily quotes and the failure alert alike, exactly as
under the direct Meta integration this replaced.

Content Template variables cannot contain newlines, so the three quotes are
sent as three separate variables (``ContentVariables`` keys "1", "2", "3")
and the template body supplies both the line breaks and the "1. "/"2. "/"3. "
numbering between them. The numbering is deliberately *not* baked into the
variable values here - the approved template already renders it, so doing
both would print it twice. Meta still rejects a variable-count mismatch, so
short-tail days pad the unused slots rather than omitting them.

Credentials below are read from configuration and are placeholders until a
Twilio account and approved templates exist; run with ``DELIVERY_MODE=console``
until then.
"""

from __future__ import annotations

import json
import time

import requests

from echoes.config import TwilioSettings
from echoes.deliver.base import Sender
from echoes.deliver.formatter import format_quote
from echoes.errors import DeliveryError
from echoes.logging_setup import get_logger, mask
from echoes.models import DailyBundle

logger = get_logger(__name__)

TWILIO_BASE_URL = "https://api.twilio.com"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Placeholder used to fill unused template slots on short-tail days.
PARAMETER_PADDING = "—"

# WhatsApp rejects template parameters containing these.
_FORBIDDEN_IN_PARAMETERS = ("\n", "\r", "\t")


def _whatsapp_address(number: str) -> str:
    """Twilio's WhatsApp channel prefixes every address with ``whatsapp:+``.

    Numbers are stored the same way as everywhere else in this project - no
    ``+``, no spaces - so the prefix is added once, here, at the API boundary.
    """
    return f"whatsapp:+{number}"


class TwilioSender(Sender):
    """Sends the daily message through Twilio's WhatsApp API."""

    def __init__(
        self,
        settings: TwilioSettings,
        *,
        expected_parameters: int = 3,
        book_separator: str = " - ",
        dry_run: bool = False,
        base_url: str = TWILIO_BASE_URL,
    ) -> None:
        self._settings = settings
        self._expected_parameters = expected_parameters
        self._book_separator = book_separator
        self._dry_run = dry_run
        self._endpoint = (
            f"{base_url.rstrip('/')}/2010-04-01/Accounts/{settings.account_sid}/Messages.json"
        )
        self._from = _whatsapp_address(str(settings.from_number))

        self._session = requests.Session()
        self._session.auth = (str(settings.account_sid), str(settings.auth_token))
        logger.debug(
            "Twilio sender ready (account_sid=%s, auth_token=%s, from=%s, dry_run=%s)",
            mask(settings.account_sid), mask(settings.auth_token), mask(settings.from_number), dry_run,
        )

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def send_daily(self, bundle: DailyBundle) -> None:
        if bundle.is_empty:
            raise DeliveryError("Refusing to send an empty message - no quotes were scheduled.")

        # Bare quote text, no leading number - the approved template already
        # supplies "1. "/"2. "/"3. ", so adding it here would double it up.
        quotes = [format_quote(quote, book_separator=self._book_separator) for quote in bundle.quotes]
        parameters = self._pad(quotes)
        recipients = self._settings.recipients

        if self._dry_run:
            logger.info(
                "DRY RUN - would send content %r to %d recipient(s) with %d parameter(s):\n%s",
                self._settings.daily_content_sid, len(recipients), len(parameters), "\n".join(parameters),
            )
            return

        # One recipient failing must not stop the others from getting today's
        # message; only a total failure is reported as undelivered.
        failed: list[str] = []
        for recipient in recipients:
            payload = self._content_payload(
                to=recipient,
                content_sid=str(self._settings.daily_content_sid),
                parameters=parameters,
            )
            try:
                message_sid = self._post(payload)
                logger.info(
                    "Delivered %d quote(s) for %s to %s via content %r (message sid %s)",
                    len(bundle.quotes), bundle.day, mask(recipient),
                    self._settings.daily_content_sid, message_sid,
                )
            except DeliveryError as exc:
                failed.append(recipient)
                logger.error("Delivery to %s failed: %s", mask(recipient), exc)

        if len(failed) == len(recipients):
            raise DeliveryError(f"Twilio delivery failed for all {len(recipients)} recipient(s).")
        if failed:
            logger.warning(
                "Delivered to %d/%d recipient(s); failed for: %s",
                len(recipients) - len(failed), len(recipients),
                ", ".join(mask(r) for r in failed),
            )

    def send_alert(self, message: str) -> None:
        """Best-effort alert. Never raises - an alert failure must not mask the
        original failure it was trying to report."""
        if not self._settings.alert_content_sid:
            logger.warning(
                "Alert not sent (TWILIO_ALERT_CONTENT_SID is unset). Alert text was: %s",
                message,
            )
            return

        text = self._sanitise(message)

        if self._dry_run:
            logger.info(
                "DRY RUN - would send alert content to %d recipient(s) with: %s",
                len(self._settings.recipients), text,
            )
            return

        for recipient in self._settings.recipients:
            payload = self._content_payload(
                to=recipient, content_sid=self._settings.alert_content_sid, parameters=[text]
            )
            try:
                self._post(payload)
                logger.info("Alert delivered to %s: %s", mask(recipient), text)
            except Exception as exc:
                logger.error(
                    "Could not deliver alert to %s (%s). Alert text was: %s", mask(recipient), exc, text
                )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _content_payload(self, *, to: str, content_sid: str, parameters: list[str]) -> dict[str, str]:
        variables = {str(index): value for index, value in enumerate(parameters, start=1)}
        return {
            "From": self._from,
            "To": _whatsapp_address(to),
            "ContentSid": content_sid,
            "ContentVariables": json.dumps(variables, ensure_ascii=False),
        }

    def _pad(self, lines: list[str]) -> list[str]:
        """Match the template's fixed parameter count.

        Meta rejects a mismatch, so a two-quote day still sends three
        parameters with the last one padded.
        """
        values = [self._sanitise(line) for line in lines[: self._expected_parameters]]
        if len(values) < self._expected_parameters:
            missing = self._expected_parameters - len(values)
            logger.info(
                "Short day: padding %d unused template parameter(s) so the send is accepted",
                missing,
            )
            values.extend([PARAMETER_PADDING] * missing)
        return values

    @staticmethod
    def _sanitise(value: str) -> str:
        cleaned = value
        for character in _FORBIDDEN_IN_PARAMETERS:
            cleaned = cleaned.replace(character, " ")
        cleaned = " ".join(cleaned.split())
        return cleaned or PARAMETER_PADDING

    def _post(self, payload: dict[str, str]) -> str:
        last_error: Exception | None = None

        for attempt in range(1, self._settings.max_retries + 1):
            try:
                response = self._session.post(
                    self._endpoint, data=payload, timeout=self._settings.timeout_seconds
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self._settings.max_retries:
                    backoff = 2 ** (attempt - 1)
                    logger.warning(
                        "Twilio request failed (attempt %d/%d): %s. Retrying in %ss.",
                        attempt, self._settings.max_retries, exc, backoff,
                    )
                    time.sleep(backoff)
                    continue
                raise DeliveryError(f"Network error calling Twilio: {exc}") from exc

            if response.status_code in _RETRYABLE_STATUS and attempt < self._settings.max_retries:
                backoff = 2 ** (attempt - 1)
                logger.warning(
                    "Twilio returned HTTP %d (attempt %d/%d). Retrying in %ss.",
                    response.status_code, attempt, self._settings.max_retries, backoff,
                )
                time.sleep(backoff)
                continue

            if not response.ok:
                raise DeliveryError(
                    f"Twilio returned HTTP {response.status_code}: {self._error_message(response)}"
                )

            return self._message_sid(response)

        raise DeliveryError(
            f"Twilio send exhausted {self._settings.max_retries} attempts: {last_error}"
        )

    @staticmethod
    def _error_message(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text[:400]
        if not isinstance(payload, dict):
            return str(payload)[:400]
        parts = [str(payload.get("message", "")), str(payload.get("more_info", ""))]
        detail = " | ".join(part for part in parts if part)
        return detail or str(payload)[:400]

    @staticmethod
    def _message_sid(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return "unknown"
        if isinstance(payload, dict):
            return str(payload.get("sid", "unknown"))
        return "unknown"
