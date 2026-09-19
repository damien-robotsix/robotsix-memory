"""Structured logging setup for robotsix-memory.

The structlog + stdlib ``ProcessorFormatter`` bridge is no longer hand-rolled
here: it is delegated to the shared
:func:`robotsix_llmio.logging.setup_structlog` helper that the rest of the
fleet (chat, central-deploy, …) already uses. ``setup_structlog`` wires a
single root ``ProcessorFormatter`` so structlog-native *and* foreign stdlib
records render through one processor chain and one renderer, stamps the active
OpenTelemetry trace id, and — with ``correlation_id=True`` — merges any value
bound through :mod:`structlog.contextvars` (e.g. the correlation id and
``owner_id`` bound by
:class:`~robotsix_memory.middleware.CorrelationIdMiddleware`) onto every event,
propagating automatically across ``await`` boundaries in async code.

JSON is emitted when ``ENVIRONMENT=production`` (for log aggregation systems
such as Datadog, ELK, CloudWatch); a coloured, human-readable console renderer
is used otherwise.
"""

from __future__ import annotations

import os
from typing import Any, TextIO

import structlog
from robotsix_llmio.logging import setup_structlog


def _is_production() -> bool:
    """True when the process runs with ``ENVIRONMENT=production``."""
    return os.environ.get("ENVIRONMENT", "").strip().lower() == "production"


def configure_logging(log_level: str = "INFO", *, stream: TextIO | None = None) -> None:
    """Initialise structlog + stdlib logging once at application startup.

    Delegates the shared processor chain and renderer to
    :func:`robotsix_llmio.logging.setup_structlog`. JSON rendering when
    ``ENVIRONMENT=production``; a coloured console renderer otherwise.
    ``correlation_id=True`` enables the contextvars merge processor so the
    correlation id and ``owner_id`` bound by
    :class:`~robotsix_memory.middleware.CorrelationIdMiddleware` appear on every
    log line.

    Args:
        log_level: Log level name (e.g. ``"INFO"``). An unrecognised value
            falls back to ``INFO``.
        stream: Target stream for the log handler. Defaults to ``sys.stdout``.
    """
    setup_structlog(
        level=log_level,
        fmt="json" if _is_production() else "console",
        loggers=("robotsix_memory",),
        stream=stream,
        correlation_id=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Return a ``structlog`` logger bound to ``name``."""
    return structlog.get_logger(name)
