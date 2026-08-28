"""WhatsApp Cloud API delivery.

Echoes never receives an inbound message, so there is never an open 24-hour
customer service window. Every message it sends is business-initiated, which
means **every** message must use a template pre-approved by Meta - the daily
quotes and the failure alert alike. That is two templates, not one.

Template parameters cannot contain newlines or tabs, so the three lines are
sent as three separate parameters and the template body supplies the line
breaks between them. A suggested body::

    1. {{1}}
    2. {{2}}
    3. {{3}}

Meta rejects a send whose parameter count does not match the template, so on a
short tail day the unused slots are padded rather than omitted.

Credentials below are read from configuration and are placeholders until the
Business account is approved; run with ``DELIVERY_MODE=console`` until then.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from echoes.config import WhatsAppSettings
from echoes.deliver.base import Sender
from echoes.deliver.formatter import format_lines
from echoes.errors import DeliveryError
from echoes.logging_setup import get_logger, mask
from echoes.models import DailyBundle

logger = get_logger(__name__)

GRAPH_BASE_URL = "https://graph.facebook.com"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Placeholder used to fill unused template slots on short-tail days.
PARAMETER_PADDING = "—"

# WhatsApp rejects template parameters containing these.
_FORBIDDEN_IN_PARAMETERS = ("\n", "\r", "\t")


class WhatsAppSender(Sender):
    """Sends the daily message through the WhatsApp Cloud API."""

    def __init__(
        self,
        settings: WhatsAppSettings,
        *,
        expected_parameters: int = 3,
        book_separator: str = " - ",
        dry_run: bool = False,
        base_url: str = GRAPH_BASE_URL,
    ) -> None:
        self._settings = settings
        self._expected_parameters = expected_parameters
        self._book_separator = book_separator
        self._dry_run = dry_run
        self._endpoint = (
            f"{base_url.rstrip('/')}/{settings.api_version}/{settings.phone_number_id}/messages"
        )

        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {settings.access_token}",
                "Content-Type": "application/json",
            }
        )
        logger.debug(
            "WhatsApp sender ready (phone_number_id=%s, token=%s, template=%s, dry_run=%s)",
            settings.phone_number_id, mask(settings.access_token),
            settings.template_name, dry_run,
        )

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def send_daily(self, bundle: DailyBundle) -> None:
        if bundle.is_empty:
            raise DeliveryError("Refusing to send an empty message - no quotes were scheduled.")

        lines = format_lines(bundle, book_separator=self._book_separator)
        parameters = self._pad(lines)

        payload = self._template_payload(
            template_name=str(self._settings.template_name), parameters=parameters
        )

        if self._dry_run:
            logger.info(
                "DRY RUN - would send template %r with %d parameter(s):\n%s",
                self._settings.template_name, len(parameters), "\n".join(parameters),
            )
            return

        message_id = self._post(payload)
        logger.info(
            "Delivered %d quote(s) for %s via template %r (message id %s)",
            len(bundle.quotes), bundle.day, self._settings.template_name, message_id,
        )

    def send_alert(self, message: str) -> None:
        """Best-effort alert. Never raises - an alert failure must not mask the
        original failure it was trying to report."""
        if not self._settings.alert_template_name:
            logger.warning(
                "Alert not sent (WHATSAPP_ALERT_TEMPLATE_NAME is unset). Alert text was: %s",
                message,
            )
            return

        text = self._sanitise(message)
        payload = self._template_payload(
            template_name=self._settings.alert_template_name, parameters=[text]
        )

        if self._dry_run:
            logger.info("DRY RUN - would send alert template with: %s", text)
            return

        try:
            self._post(payload)
            logger.info("Alert delivered: %s", text)
        except Exception as exc:  # noqa: BLE001 - alerting must never raise
            logger.error("Could not deliver alert (%s). Alert text was: %s", exc, text)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _template_payload(self, *, template_name: str, parameters: list[str]) -> dict[str, Any]:
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self._settings.recipient,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": self._settings.template_language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": value} for value in parameters
                        ],
                    }
                ],
            },
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

    def _post(self, payload: dict[str, Any]) -> str:
        last_error: Exception | None = None

        for attempt in range(1, self._settings.max_retries + 1):
            try:
                response = self._session.post(
                    self._endpoint, json=payload, timeout=self._settings.timeout_seconds
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self._settings.max_retries:
                    backoff = 2 ** (attempt - 1)
                    logger.warning(
                        "WhatsApp request failed (attempt %d/%d): %s. Retrying in %ss.",
                        attempt, self._settings.max_retries, exc, backoff,
                    )
                    time.sleep(backoff)
                    continue
                raise DeliveryError(f"Network error calling WhatsApp Cloud API: {exc}") from exc

            if response.status_code in _RETRYABLE_STATUS and attempt < self._settings.max_retries:
                backoff = 2 ** (attempt - 1)
                logger.warning(
                    "WhatsApp returned HTTP %d (attempt %d/%d). Retrying in %ss.",
                    response.status_code, attempt, self._settings.max_retries, backoff,
                )
                time.sleep(backoff)
                continue

            if not response.ok:
                raise DeliveryError(
                    f"WhatsApp Cloud API returned HTTP {response.status_code}: "
                    f"{self._error_message(response)}"
                )

            return self._message_id(response)

        raise DeliveryError(
            f"WhatsApp send exhausted {self._settings.max_retries} attempts: {last_error}"
        )

    @staticmethod
    def _error_message(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text[:400]
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        parts = [
            str(error.get("message", "")),
            str(error.get("error_data", {}).get("details", "")),
        ]
        detail = " | ".join(part for part in parts if part)
        return detail or str(payload)[:400]

    @staticmethod
    def _message_id(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return "unknown"
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if isinstance(messages, list) and messages:
            return str(messages[0].get("id", "unknown"))
        return "unknown"
