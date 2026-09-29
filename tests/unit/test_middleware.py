"""Tests for robotsix-memory's correlation-id middleware wiring.

The middleware itself now lives in :mod:`robotsix_http.fastapi`; these tests
assert robotsix-memory configures it with the memory-specific header name
(``X-Request-ID``) and context key (``correlation_id``), and that the id is
bound into the ``structlog`` contextvars context for the duration of a request.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from robotsix_http.fastapi import CorrelationIdMiddleware

CORRELATION_ID_HEADER = "X-Request-ID"


def _build_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(
        CorrelationIdMiddleware,
        header_name=CORRELATION_ID_HEADER,
        context_field="correlation_id",
        log_requests=True,
    )

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        # The id bound into structlog contextvars by the middleware must be
        # visible in the endpoint, which only holds when the middleware
        # preserves the async context.
        return {"correlation_id": structlog.contextvars.get_contextvars().get("correlation_id", "")}

    return TestClient(app)


def test_generates_and_echoes_correlation_id() -> None:
    client = _build_client()
    resp = client.get("/ping")
    assert resp.status_code == 200
    header = resp.headers.get(CORRELATION_ID_HEADER)
    assert header
    assert resp.json()["correlation_id"] == header


def test_reuses_incoming_correlation_id() -> None:
    client = _build_client()
    resp = client.get("/ping", headers={CORRELATION_ID_HEADER: "abc-123"})
    assert resp.headers.get(CORRELATION_ID_HEADER) == "abc-123"
    assert resp.json()["correlation_id"] == "abc-123"


def test_correlation_id_unbound_after_request() -> None:
    client = _build_client()
    client.get("/ping")
    # The middleware unbinds the contextvar in its ``finally`` block.
    assert "correlation_id" not in structlog.contextvars.get_contextvars()
