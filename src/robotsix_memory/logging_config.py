"""Structured logging setup for robotsix-memory using ``structlog``.

Emits JSON in production (``ENVIRONMENT=production``) for log aggregation
systems (Datadog, ELK, CloudWatch) and coloured, human-readable console
output in development. ``structlog.contextvars.merge_contextvars`` folds any
context bound with ``structlog.contextvars.bind_contextvars`` (e.g. the
correlation id and ``owner_id``) into every event, and because that context
lives in :mod:`contextvars` it propagates automatically across ``await``
boundaries in async code.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import structlog


def _is_production() -> bool:
    """True when the process runs with ``ENVIRONMENT=production``."""
    return os.environ.get("ENVIRONMENT", "").strip().lower() == "production"


def configure_logging(log_level: str = "INFO") -> None:
    """Initialise ``structlog`` once at application startup.

    JSON rendering when ``ENVIRONMENT=production``; a coloured console
    renderer otherwise. The shared processor chain timestamps every event,
    adds the log level, renders exception info, and merges the contextvars
    context so correlation ids and ``owner_id`` appear on every line.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer() if _is_production() else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Return a ``structlog`` logger bound to ``name``."""
    return structlog.get_logger(name)
