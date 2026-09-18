"""Tests for structlog initialisation in ``logging_config``."""

from __future__ import annotations

import pytest
import structlog

from robotsix_memory.logging_config import configure_logging, get_logger


def test_production_env_uses_json_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    configure_logging("INFO")
    processors = structlog.get_config()["processors"]
    assert isinstance(processors[-1], structlog.processors.JSONRenderer)


def test_development_env_uses_console_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    configure_logging("DEBUG")
    processors = structlog.get_config()["processors"]
    assert isinstance(processors[-1], structlog.dev.ConsoleRenderer)


def test_unknown_level_falls_back_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    # An unrecognised level must not raise — it defaults to INFO.
    configure_logging("NONSENSE")
    get_logger("test").info("smoke")


def test_merge_contextvars_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The context-merging processor is wired so bound ids reach every event."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    configure_logging("INFO")
    processors = structlog.get_config()["processors"]
    assert structlog.contextvars.merge_contextvars in processors


def test_bound_contextvars_are_rendered() -> None:
    """Values bound via contextvars are folded into the rendered output."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(correlation_id="abc", owner_id="operator")
    try:
        rendered = structlog.contextvars.merge_contextvars(None, "info", {"event": "e"})
        assert rendered["correlation_id"] == "abc"
        assert rendered["owner_id"] == "operator"
    finally:
        structlog.contextvars.clear_contextvars()
