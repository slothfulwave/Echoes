"""Class 1 - Collect. Reads quotes from Notion; never writes to it."""

from __future__ import annotations

from echoes.collect.collector import QuoteCollector
from echoes.collect.notion_api import NotionAPI

__all__ = ["NotionAPI", "QuoteCollector"]
