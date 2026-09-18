"""Tests for ``CorrelationIdMiddleware`` correlation-id handling."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from robotsix_memory.middleware import (
    CORRELATION_ID_HEADER,
    CorrelationIdMiddleware,
    correlation_id_ctx,
)


def _build_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        # The contextvar set by the middleware must be visible in the endpoint,
        # which only holds when the middleware preserves the async context.
        return {"correlation_id": correlation_id_ctx.get()}

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


def test_correlation_id_reset_after_request() -> None:
    client = _build_client()
    client.get("/ping")
    # The middleware resets the contextvar in its ``finally`` block.
    assert correlation_id_ctx.get() == ""
