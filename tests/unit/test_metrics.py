"""Unit tests for the opt-in Prometheus metrics gate (robotsix_memory.main).

Metrics instrumentation and the separate ``/metrics`` exposition server are
off unless the deployment sets ``ENABLE_METRICS`` to a truthy value. These
tests pin that parsing and confirm the test environment keeps metrics
disabled (no ``/metrics`` route is mounted on the fleet-facing app).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from robotsix_memory.main import _metrics_enabled, app


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on", " true "])
def test_metrics_enabled_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ENABLE_METRICS", value)
    assert _metrics_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "nope"])
def test_metrics_disabled_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ENABLE_METRICS", value)
    assert _metrics_enabled() is False


def test_metrics_disabled_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_METRICS", raising=False)
    assert _metrics_enabled() is False


def test_metrics_endpoint_not_on_fleet_app() -> None:
    """The scraped endpoint lives on its own port, never on the API app."""
    client = TestClient(app)
    assert client.get("/metrics").status_code == 404
