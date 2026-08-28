"""Configuration.

One rule governs this module: **configuration always comes from the process
environment.** The only difference between local and CI is how those variables
got there.

* Locally, ``.env`` is read from the project root and loaded into the
  environment. ``.env`` is gitignored and never leaves the machine.
* On GitHub Actions, ``GITHUB_ACTIONS=true`` is set by the runner, so the
  ``.env`` load is skipped entirely and values come from repository secrets
  injected by the workflow.

Because both paths end at ``os.environ``, the application code below this
module cannot tell the difference - and there is no code path where a secret
could be read from a file that might get committed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from echoes.errors import ConfigurationError
from echoes.logging_setup import get_logger

logger = get_logger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def project_root() -> Path:
    """Repository root - three levels up from ``src/echoes/config.py``."""
    return Path(__file__).resolve().parents[2]


def running_in_github_actions() -> bool:
    return os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true"


def load_environment() -> str:
    """Populate ``os.environ`` for the current execution context.

    Returns a short label describing where configuration came from, purely so
    it can be logged. Existing environment variables always win over ``.env``
    (``override=False``), so an explicit shell export beats the file.
    """
    if running_in_github_actions():
        return "github-actions (repository secrets)"

    env_file = project_root() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
        return f"local ({env_file.name})"

    return "local (no .env found - using shell environment only)"


def _get(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    raw = os.getenv(name)
    value = raw.strip() if raw is not None else None
    if not value:
        if required:
            raise ConfigurationError(
                f"Required environment variable {name!r} is missing or empty. "
                "Set it in .env for local runs, or as a repository secret for GitHub Actions."
            )
        return default
    return value


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"Environment variable {name!r} must be an integer, got {raw!r}.") from exc


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    raise ConfigurationError(f"Environment variable {name!r} must be a boolean, got {raw!r}.")


def _get_list(name: str, *, required: bool = False) -> list[str]:
    """Comma-separated environment variable, e.g. multiple WhatsApp recipients."""
    raw = _get(name, required=required)
    values = [item.strip() for item in raw.split(",")] if raw else []
    values = [item for item in values if item]
    if required and not values:
        raise ConfigurationError(
            f"Environment variable {name!r} must contain at least one value."
        )
    return values


def _get_date(name: str, default: str) -> date:
    raw = _get(name, default)
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise ConfigurationError(
            f"Environment variable {name!r} must be an ISO date (YYYY-MM-DD), got {raw!r}."
        ) from exc


@dataclass(frozen=True, slots=True)
class NotionSettings:
    """Notion credentials and the two source filters."""

    api_key: str
    api_version: str
    timeout_seconds: int
    max_retries: int

    # Books! Books! Books! -> Status = Completed AND Completion Date >= cutoff
    books_database_id: str
    books_status_property: str
    books_status_value: str
    books_date_property: str
    books_completed_on_or_after: date

    # The Me Section -> Tags contains "Quote"
    me_section_database_id: str
    me_section_tag_property: str
    me_section_tag_value: str


@dataclass(frozen=True, slots=True)
class WhatsAppSettings:
    """WhatsApp Cloud API credentials and template names.

    Placeholders until the Business account exists; ``DELIVERY_MODE=console``
    keeps the rest of the system runnable in the meantime.
    """

    api_version: str
    phone_number_id: str | None
    access_token: str | None
    recipients: list[str]
    template_name: str | None
    template_language: str
    alert_template_name: str | None
    timeout_seconds: int
    max_retries: int


@dataclass(frozen=True, slots=True)
class Settings:
    """Fully resolved application configuration."""

    source: str
    log_level: str
    timezone: ZoneInfo
    timezone_name: str
    state_dir: Path
    dry_run: bool
    random_seed: int | None

    quotes_per_day_books: int
    quotes_per_day_standalone: int
    quotes_per_day_books_fallback: int

    book_separator: str
    attribution_separator: str

    delivery_mode: str
    alerts_enabled: bool
    sunday_refresh_enabled: bool
    refresh_weekday: int  # Monday=0 ... Sunday=6

    notion: NotionSettings
    whatsapp: WhatsAppSettings

    @classmethod
    def from_env(cls) -> Settings:
        source = load_environment()

        timezone_name = str(_get("TIMEZONE", "Asia/Kolkata"))
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError(f"Unknown TIMEZONE {timezone_name!r}.") from exc

        delivery_mode = str(_get("DELIVERY_MODE", "console")).lower()
        if delivery_mode not in {"console", "whatsapp"}:
            raise ConfigurationError(
                f"DELIVERY_MODE must be 'console' or 'whatsapp', got {delivery_mode!r}."
            )

        state_dir_raw = str(_get("STATE_DIR", "state"))
        state_dir = Path(state_dir_raw)
        if not state_dir.is_absolute():
            state_dir = project_root() / state_dir

        seed_raw = _get("RANDOM_SEED")
        random_seed = int(seed_raw) if seed_raw else None

        whatsapp_required = delivery_mode == "whatsapp"

        notion = NotionSettings(
            api_key=str(_get("NOTION_API_KEY", required=True)),
            api_version=str(_get("NOTION_API_VERSION", "2022-06-28")),
            timeout_seconds=_get_int("NOTION_TIMEOUT_SECONDS", 30),
            max_retries=_get_int("NOTION_MAX_RETRIES", 3),
            books_database_id=str(_get("NOTION_BOOKS_DATABASE_ID", required=True)),
            books_status_property=str(_get("NOTION_BOOKS_STATUS_PROPERTY", "Status")),
            books_status_value=str(_get("NOTION_BOOKS_STATUS_VALUE", "Completed")),
            books_date_property=str(_get("NOTION_BOOKS_DATE_PROPERTY", "Completion Date")),
            books_completed_on_or_after=_get_date("NOTION_BOOKS_COMPLETED_ON_OR_AFTER", "2024-04-24"),
            me_section_database_id=str(_get("NOTION_ME_SECTION_DATABASE_ID", required=True)),
            me_section_tag_property=str(_get("NOTION_ME_SECTION_TAG_PROPERTY", "Tags")),
            me_section_tag_value=str(_get("NOTION_ME_SECTION_TAG_VALUE", "Quote")),
        )

        whatsapp = WhatsAppSettings(
            api_version=str(_get("WHATSAPP_API_VERSION", "v21.0")),
            phone_number_id=_get("WHATSAPP_PHONE_NUMBER_ID", required=whatsapp_required),
            access_token=_get("WHATSAPP_ACCESS_TOKEN", required=whatsapp_required),
            recipients=_get_list("WHATSAPP_RECIPIENT_NUMBERS", required=whatsapp_required),
            template_name=_get("WHATSAPP_TEMPLATE_NAME", required=whatsapp_required),
            template_language=str(_get("WHATSAPP_TEMPLATE_LANGUAGE", "en")),
            alert_template_name=_get("WHATSAPP_ALERT_TEMPLATE_NAME"),
            timeout_seconds=_get_int("WHATSAPP_TIMEOUT_SECONDS", 30),
            max_retries=_get_int("WHATSAPP_MAX_RETRIES", 3),
        )

        settings = cls(
            source=source,
            log_level=str(_get("LOG_LEVEL", "INFO")).upper(),
            timezone=timezone,
            timezone_name=timezone_name,
            state_dir=state_dir,
            dry_run=_get_bool("DRY_RUN", False),
            random_seed=random_seed,
            quotes_per_day_books=_get_int("QUOTES_PER_DAY_BOOKS", 2),
            quotes_per_day_standalone=_get_int("QUOTES_PER_DAY_STANDALONE", 1),
            quotes_per_day_books_fallback=_get_int("QUOTES_PER_DAY_BOOKS_FALLBACK", 3),
            book_separator=os.getenv("BOOK_SEPARATOR", " - "),
            attribution_separator=os.getenv("ATTRIBUTION_SEPARATOR", " — "),
            delivery_mode=delivery_mode,
            alerts_enabled=_get_bool("ALERTS_ENABLED", True),
            sunday_refresh_enabled=_get_bool("SUNDAY_REFRESH_ENABLED", True),
            refresh_weekday=_get_int("REFRESH_WEEKDAY", 6),
            notion=notion,
            whatsapp=whatsapp,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.quotes_per_day_books < 1:
            raise ConfigurationError("QUOTES_PER_DAY_BOOKS must be at least 1.")
        if self.quotes_per_day_standalone < 0:
            raise ConfigurationError("QUOTES_PER_DAY_STANDALONE cannot be negative.")
        if self.quotes_per_day_books_fallback < self.quotes_per_day_books:
            raise ConfigurationError(
                "QUOTES_PER_DAY_BOOKS_FALLBACK must be >= QUOTES_PER_DAY_BOOKS; "
                "the fallback exists to make up for the missing standalone quote."
            )
        if not 0 <= self.refresh_weekday <= 6:
            raise ConfigurationError("REFRESH_WEEKDAY must be 0 (Monday) through 6 (Sunday).")

    def describe(self) -> str:
        """Human-readable, secret-free summary for the startup log line."""
        return (
            f"config source={self.source}; timezone={self.timezone_name}; "
            f"delivery={self.delivery_mode}; dry_run={self.dry_run}; "
            f"ratio={self.quotes_per_day_books}+{self.quotes_per_day_standalone} "
            f"(fallback {self.quotes_per_day_books_fallback}+0); "
            f"state_dir={self.state_dir}"
        )
