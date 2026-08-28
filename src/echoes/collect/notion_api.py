"""Thin Notion REST client.

Deliberately small: pagination, retries, and error translation. It knows
nothing about books, quotes, or callouts - that lives in ``collector.py``.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import requests

from echoes.errors import NotionAPIError
from echoes.logging_setup import get_logger, mask

logger = get_logger(__name__)

BASE_URL = "https://api.notion.com/v1"

# 429 is rate limiting (Notion sends Retry-After); 5xx are transient.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class NotionAPI:
    """HTTP access to the Notion API."""

    def __init__(
        self,
        api_key: str,
        *,
        api_version: str = "2022-06-28",
        timeout_seconds: int = 30,
        max_retries: int = 3,
        base_url: str = BASE_URL,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._base_url = base_url.rstrip("/")

        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Notion-Version": api_version,
                "Content-Type": "application/json",
                "User-Agent": "echoes/1.0 (personal quote resurfacing)",
            }
        )
        logger.debug(
            "Notion client ready (version=%s, key=%s, timeout=%ss)",
            api_version,
            mask(api_key),
            timeout_seconds,
        )

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> NotionAPI:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._session.request(
                    method, url, json=json, params=params, timeout=self._timeout
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self._max_retries:
                    backoff = 2 ** (attempt - 1)
                    logger.warning(
                        "Notion request failed (%s %s), attempt %d/%d: %s. Retrying in %ss.",
                        method, path, attempt, self._max_retries, exc, backoff,
                    )
                    time.sleep(backoff)
                    continue
                raise NotionAPIError(f"Network error calling Notion {method} {path}: {exc}") from exc

            if response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                backoff = self._retry_delay(response, attempt)
                logger.warning(
                    "Notion returned HTTP %d for %s %s (attempt %d/%d). Retrying in %ss.",
                    response.status_code, method, path, attempt, self._max_retries, backoff,
                )
                time.sleep(backoff)
                continue

            if not response.ok:
                payload = self._safe_json(response)
                code = payload.get("code")
                message = payload.get("message", response.text[:400])
                raise NotionAPIError(
                    f"Notion {method} {path} failed with HTTP {response.status_code}: {message}",
                    status_code=response.status_code,
                    code=code,
                )

            return self._safe_json(response)

        raise NotionAPIError(
            f"Notion {method} {path} exhausted {self._max_retries} attempts: {last_error}"
        )

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> int:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(1, int(float(retry_after)))
            except ValueError:
                pass
        return 2 ** (attempt - 1)

    @staticmethod
    def _safe_json(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------
    def query_database(
        self, database_id: str, *, filter_: dict[str, Any] | None = None, page_size: int = 100
    ) -> Iterator[dict[str, Any]]:
        """Yield every page in a database, following pagination cursors."""
        cursor: str | None = None
        page_count = 0

        while True:
            body: dict[str, Any] = {"page_size": page_size}
            if filter_:
                body["filter"] = filter_
            if cursor:
                body["start_cursor"] = cursor

            payload = self._request("POST", f"/databases/{database_id}/query", json=body)
            results = payload.get("results", [])
            page_count += 1
            logger.debug(
                "Database %s query page %d returned %d result(s)",
                database_id, page_count, len(results),
            )
            yield from results

            if not payload.get("has_more"):
                return
            cursor = payload.get("next_cursor")
            if not cursor:
                return

    def list_block_children(self, block_id: str, *, page_size: int = 100) -> Iterator[dict[str, Any]]:
        """Yield every direct child block of a page or block."""
        cursor: str | None = None

        while True:
            params: dict[str, Any] = {"page_size": page_size}
            if cursor:
                params["start_cursor"] = cursor

            payload = self._request("GET", f"/blocks/{block_id}/children", params=params)
            yield from payload.get("results", [])

            if not payload.get("has_more"):
                return
            cursor = payload.get("next_cursor")
            if not cursor:
                return
