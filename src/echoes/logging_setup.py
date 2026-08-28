"""Logging configuration.

Everything goes to stdout so that GitHub Actions captures it in the run log.
Log lines are prefixed with the module name, which makes it obvious which
conceptual class (collect / playlist / deliver) produced a given line.
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging. Safe to call more than once."""
    global _configured

    resolved = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    if _configured:
        root.setLevel(resolved)
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved)

    # These libraries are chatty at DEBUG and leak URLs with tokens in them.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger."""
    return logging.getLogger(name)


def mask(secret: str | None, *, visible: int = 4) -> str:
    """Render a secret safe for logging: ``secret_abcd1234`` -> ``***1234``.

    Never log a raw token. This exists so that "is the right key loaded?"
    can be answered from a log file without exposing the key itself.
    """
    if not secret:
        return "<unset>"
    if len(secret) <= visible:
        return "*" * len(secret)
    return f"***{secret[-visible:]}"
