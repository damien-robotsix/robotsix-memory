"""Thin async client for the Hindsight memory engine's REST API.

The wrapper keeps the fleet-facing contract (``/remember``, ``/recall``,
``/reflect``) stable while Hindsight remains a swappable implementation
detail behind this module. Endpoint shapes follow the Hindsight v0.9 API
(``/v1/default/banks/{bank_id}/...``).
"""

from __future__ import annotations

import re
from typing import Any

import httpx

_BANK_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


class HindsightError(Exception):
    """Raised when Hindsight returns a non-2xx response or is unreachable.

    Carries the upstream status and body so callers (and ultimately the
    chat agent) see the engine's real error instead of a laundered one.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def bank_id(prefix: str, owner_id: str) -> str:
    """Map an owner id to a Hindsight bank id (one bank per owner)."""
    safe_owner = _BANK_SAFE_RE.sub("-", owner_id.strip()) or "default"
    return f"{prefix}-{safe_owner}"


class HindsightClient:
    """Async HTTP client bound to one Hindsight server."""

    def __init__(self, base_url: str, timeout: float, retain_timeout: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retain_timeout = retain_timeout

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout or self._timeout) as client:
                resp = await client.request(method, url, json=json_body, params=params)
        except httpx.HTTPError as exc:
            raise HindsightError(f"hindsight unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise HindsightError(
                f"hindsight returned {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
            )
        if not resp.content:
            return {}
        return resp.json()

    async def ping(self) -> bool:
        """True when the Hindsight API answers at all."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url}/")
        except httpx.HTTPError:
            return False
        return resp.status_code < 500

    async def retain(
        self,
        bank: str,
        content: str,
        *,
        timestamp: str | None = None,
        context: str | None = None,
        tags: list[str] | None = None,
        document_id: str | None = None,
    ) -> Any:
        item: dict[str, Any] = {"content": content}
        if timestamp:
            item["timestamp"] = timestamp
        if context:
            item["context"] = context
        if tags:
            item["tags"] = tags
        if document_id:
            item["document_id"] = document_id
        return await self._request(
            "POST",
            f"/v1/default/banks/{bank}/memories",
            json_body={"items": [item], "async": False},
            timeout=self._retain_timeout,
        )

    async def recall(
        self,
        bank: str,
        query: str,
        *,
        limit: int,
        tags: list[str] | None = None,
    ) -> Any:
        body: dict[str, Any] = {"query": query}
        if tags:
            body["tags"] = tags
        result = await self._request(
            "POST", f"/v1/default/banks/{bank}/memories/recall", json_body=body
        )
        # The engine budgets by tokens, not count — apply the caller's limit
        # to the ranked results so the wrapper contract stays simple.
        if isinstance(result, dict) and isinstance(result.get("results"), list):
            result["results"] = result["results"][:limit]
        return result

    async def reflect(self, bank: str, query: str) -> Any:
        return await self._request(
            "POST",
            f"/v1/default/banks/{bank}/reflect",
            json_body={"query": query},
            timeout=self._retain_timeout,
        )
