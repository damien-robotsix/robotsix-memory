"""Correlation-id middleware for robotsix-memory.

A pure-ASGI middleware that assigns (or reuses) a correlation id per request
and binds it into the ``structlog`` contextvars context, so every log line
emitted while handling the request carries the id. It is deliberately a raw
ASGI middleware rather than Starlette's ``BaseHTTPMiddleware`` because the
latter runs the endpoint in a separate task, which breaks
:mod:`contextvars` propagation; a raw middleware awaits the app in the same
context so the bound id reaches the endpoint and the Hindsight client across
every ``await`` boundary.
"""

from __future__ import annotations

import time
import uuid
from contextvars import ContextVar

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from robotsix_memory.logging_config import get_logger

#: Request/response header carrying the correlation id.
CORRELATION_ID_HEADER = "X-Request-ID"

#: Correlation id for the in-flight request, propagated across await points.
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")

logger = get_logger("robotsix_memory.request")


class CorrelationIdMiddleware:
    """Bind a correlation id to the logging context for each HTTP request."""

    def __init__(self, app: ASGIApp, header_name: str = CORRELATION_ID_HEADER) -> None:
        self._app = app
        self._header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        header_key = self._header_name.lower().encode("latin-1")
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(header_key)
        correlation_id = incoming.decode("latin-1") if incoming else uuid.uuid4().hex

        method = scope.get("method", "")
        path = scope.get("path", "")

        token = correlation_id_ctx.set(correlation_id)
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        logger.info("request.start", method=method, path=path)
        start = time.monotonic()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                raw_headers = list(message.get("headers") or [])
                raw_headers.append((header_key, correlation_id.encode("latin-1")))
                message = {**message, "headers": raw_headers}
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            logger.info(
                "request.end",
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
            )
            structlog.contextvars.unbind_contextvars("correlation_id")
            correlation_id_ctx.reset(token)
