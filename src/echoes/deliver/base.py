"""The delivery interface.

Two implementations exist: :class:`~echoes.deliver.console.ConsoleSender` for
local development and for the period before a Twilio account and approved
templates exist, and :class:`~echoes.deliver.twilio.TwilioSender` for
production. Everything upstream of this interface is identical in both cases.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from echoes.models import DailyBundle


class Sender(ABC):
    """Delivers the daily message and operational alerts."""

    @abstractmethod
    def send_daily(self, bundle: DailyBundle) -> None:
        """Send the day's quotes. Raises :class:`DeliveryError` on failure."""

    @abstractmethod
    def send_alert(self, message: str) -> None:
        """Send a failure alert. Must never raise - alerting is best-effort."""

    def close(self) -> None:  # pragma: no cover  # noqa: B027 - optional; not every Sender holds a resource
        """Release any held resources."""

    def __enter__(self) -> Sender:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
