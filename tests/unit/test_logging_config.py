"""Tests for structlog initialisation in ``logging_config``.

``configure_logging`` delegates the structlog + stdlib ``ProcessorFormatter``
bridge to :func:`robotsix_llmio.logging.setup_structlog`. These tests verify
JSON output in production, human-readable console output in development,
graceful handling of an unknown level, and that ids bound via
:mod:`structlog.contextvars` (correlation id, ``owner_id``) are merged onto
every record.
"""

from __future__ import annotations

import io
import json

import pytest
import structlog

from robotsix_memory.logging_config import configure_logging, get_logger


def test_production_env_uses_json_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    buf = io.StringIO()
    structlog.contextvars.clear_contextvars()
    configure_logging("INFO", stream=buf)
    try:
        get_logger("robotsix_memory.test").info("hello")
    finally:
        structlog.contextvars.clear_contextvars()

    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    record = json.loads(lines[-1])
    assert record["event"] == "hello"
    assert record["level"] == "info"


def test_development_env_uses_console_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    buf = io.StringIO()
    configure_logging("DEBUG", stream=buf)
    get_logger("robotsix_memory.test").info("hello")

    output = buf.getvalue()
    assert "hello" in output
    # The console renderer does not emit JSON.
    with pytest.raises(json.JSONDecodeError):
        json.loads(output.strip().splitlines()[-1])


def test_unknown_level_falls_back_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    buf = io.StringIO()
    # An unrecognised level must not raise — it defaults to INFO.
    configure_logging("NONSENSE", stream=buf)
    get_logger("robotsix_memory.test").info("smoke")
    assert "smoke" in buf.getvalue()


def test_bound_contextvars_are_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Values bound via contextvars are folded into every rendered record."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    buf = io.StringIO()
    structlog.contextvars.clear_contextvars()
    configure_logging("INFO", stream=buf)
    structlog.contextvars.bind_contextvars(correlation_id="abc", owner_id="operator")
    try:
        get_logger("robotsix_memory.test").info("event")
    finally:
        structlog.contextvars.clear_contextvars()

    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    record = json.loads(lines[-1])
    assert record["correlation_id"] == "abc"
    assert record["owner_id"] == "operator"
