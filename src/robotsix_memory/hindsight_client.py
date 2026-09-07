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
from robotsix_http import ExternalHTTPError, RetryClient

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


# Recall sizing. Hindsight's recall pipeline retrieves a candidate pool sized
# by ``budget`` ("low"/"mid"/"high"), cross-encoder reranks EVERY candidate,
# then keeps results until ``max_tokens`` is spent. On the fleet's CPU-only
# VPS the rerank costs ~0.1 s per candidate, so the engine defaults
# (budget="mid" => ~150 candidates, max_tokens=4096 => ~78 results) meant
# ~15 s per recall on 2026-09-07 (mean 16.2 s, p90 30.4 s over 83 recalls)
# while chat sliced the answer to 8 results and timed out at 20 s. Size the
# engine's work to what the caller will actually keep.
RECALL_BUDGET_LOW_MAX_LIMIT = 10  # chat's per-turn recall asks for <= 8
RECALL_BUDGET_MID_MAX_LIMIT = 50
RECALL_TOKENS_PER_RESULT = 160  # one rendered memory ~ 160 tokens (observed)
RECALL_MIN_MAX_TOKENS = 512
RECALL_ENGINE_MAX_TOKENS = 4096  # Hindsight's own default ceiling


def recall_budget_for(limit: int) -> str:
    """Pick the engine candidate budget matching how many results are kept."""
    if limit <= RECALL_BUDGET_LOW_MAX_LIMIT:
        return "low"
    if limit <= RECALL_BUDGET_MID_MAX_LIMIT:
        return "mid"
    return "high"


def recall_max_tokens_for(limit: int) -> int:
    """Token cap so the engine's token filter stops near ``limit`` results.

    ~160 tokens per rendered memory: 8 results => 1280 tokens instead of
    filling 4096 with 78 results the wrapper would discard. Clamped to
    [512, 4096] so tiny limits still get a few results and large ones
    never exceed the engine's default ceiling.
    """
    return min(
        RECALL_ENGINE_MAX_TOKENS, max(RECALL_MIN_MAX_TOKENS, limit * RECALL_TOKENS_PER_RESULT)
    )


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
            async with httpx.AsyncClient(timeout=timeout or self._timeout) as http_client:
                client = RetryClient(http_client)
                resp = await client.request(method, url, json=json_body, params=params)
        except ExternalHTTPError as exc:
            # RetryClient mapped a 401/403/429/5xx after exhausting retries.
            raise HindsightError(
                f"hindsight returned {exc.status_code}: {exc.response.text[:500]}",
                status_code=exc.status_code,
            ) from exc
        except httpx.HTTPStatusError as exc:
            # Other 4xx: raise_for_status fired but RetryClient left it unmapped.
            raise HindsightError(
                f"hindsight returned {exc.response.status_code}: {exc.response.text[:500]}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise HindsightError(f"hindsight unreachable: {exc}") from exc
        if not resp.content:
            return {}
        return resp.json()

    async def ping(self) -> bool:
        """True when the Hindsight API answers at all."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as http_client:
                client = RetryClient(http_client)
                resp = await client.get(f"{self._base_url}/")
        except ExternalHTTPError as exc:
            # The engine answered with an auth/rate-limit/other client error;
            # a status < 500 still means the API is reachable.
            return exc.status_code < 500
        except httpx.HTTPStatusError as exc:
            return exc.response.status_code < 500
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
        update_mode: str | None = None,
        background: bool = False,
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
        if update_mode:
            item["update_mode"] = update_mode
        return await self._request(
            "POST",
            f"/v1/default/banks/{bank}/memories",
            json_body={"items": [item], "async": background},
            timeout=self._retain_timeout,
        )

    async def recall(
        self,
        bank: str,
        query: str,
        *,
        limit: int,
        tags: list[str] | None = None,
        budget: str | None = None,
    ) -> Any:
        body: dict[str, Any] = {
            "query": query,
            "budget": budget or recall_budget_for(limit),
            "max_tokens": recall_max_tokens_for(limit),
        }
        if tags:
            body["tags"] = tags
        result = await self._request(
            "POST", f"/v1/default/banks/{bank}/memories/recall", json_body=body
        )
        # The engine budgets by tokens, not count — the sizing above only
        # bounds its work; apply the caller's limit to the ranked results so
        # the wrapper contract (at most ``limit`` results) stays guaranteed.
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
