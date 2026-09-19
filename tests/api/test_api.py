"""Endpoint/contract tests for the robotsix-memory wrapper.

These drive the FastAPI app through ``TestClient`` and assert the
fleet-facing HTTP contract (status codes, response envelopes, error
surfacing). Hindsight is mocked at the transport layer via the shared
``hindsight_mock`` fixture; pure client request/response shaping lives
in ``tests/unit``.
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace
from typing import Literal, get_args, get_origin

import httpx
import pytest
from fastapi.testclient import TestClient
from robotsix_http.fastapi import (
    assert_chat_skill_route_parity,
    documented_routes,
    parse_chat_skill_frontmatter,
)

from robotsix_memory.chat_skill import chat_skill
from robotsix_memory.hindsight_client import (
    RECALL_BUDGET_LOW_MAX_LIMIT,
    RECALL_BUDGET_MID_MAX_LIMIT,
)
from robotsix_memory.main import ReflectRequest, RememberRequest, app, settings

client = TestClient(app)


def test_health_live() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_standard_shape() -> None:
    """The fleet-standard /health from create_health_router()."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_reports_hindsight_down(hindsight_mock: SimpleNamespace) -> None:
    hindsight_mock.routes["ping"].mock(side_effect=httpx.ConnectError("refused"))
    resp = client.get("/health/hindsight")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "hindsight": "unreachable"}


def test_health_reports_hindsight_ok(hindsight_mock: SimpleNamespace) -> None:
    resp = client.get("/health/hindsight")
    assert resp.json() == {"status": "ok", "hindsight": "ok"}


def test_unhandled_exception_returns_500_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected error hits the catch-all handler and yields the envelope."""

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr("robotsix_memory.main.client.reflect", _boom)
    safe_client = TestClient(app, raise_server_exceptions=False)
    resp = safe_client.post("/reflect", json={"query": "q", "owner_id": "operator"})
    assert resp.status_code == 500
    assert resp.json() == {"error": {"code": "internal_error", "detail": "Internal Server Error"}}


def test_chat_skill_shape() -> None:
    """The descriptor is served as validated text/markdown+frontmatter."""
    resp = client.get("/chat-skill")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    frontmatter = parse_chat_skill_frontmatter(resp.text)
    assert frontmatter.name == "robotsix-memory"
    assert frontmatter.description
    assert {"/remember", "/recall", "/reflect"} <= documented_routes(resp.text)


def test_remember_maps_to_retain(hindsight_mock: SimpleNamespace) -> None:
    resp = client.post(
        "/remember",
        json={
            "content": "Damien prefers merge-now over approve on MR gates.",
            "owner_id": "operator",
            "tags": ["preferences"],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["stored"] is True
    assert hindsight_mock.routes["retain"].called


def test_remember_surfaces_engine_error_verbatim(hindsight_mock: SimpleNamespace) -> None:
    hindsight_mock.routes["retain"].mock(
        return_value=httpx.Response(422, json={"detail": "content is required"})
    )
    resp = client.post("/remember", json={"content": "x", "owner_id": "operator"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "hindsight_error"
    assert "content is required" in body["error"]["detail"]


def test_remember_502_when_engine_unreachable(hindsight_mock: SimpleNamespace) -> None:
    hindsight_mock.routes["retain"].mock(side_effect=httpx.ConnectError("refused"))
    resp = client.post("/remember", json={"content": "x", "owner_id": "operator"})
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["code"] == "hindsight_error"
    assert "unreachable" in body["error"]["detail"]


def test_remember_rejects_missing_owner() -> None:
    resp = client.post("/remember", json={"content": "x"})
    assert resp.status_code == 422


def test_recall_posts_query_and_slices_to_limit(hindsight_mock: SimpleNamespace) -> None:
    engine_results = {"results": [{"text": f"fact {i}"} for i in range(20)], "entities": {}}
    hindsight_mock.routes["recall"].mock(return_value=httpx.Response(200, json=engine_results))
    resp = client.get(
        "/recall", params={"query": "preferences", "owner_id": "operator", "limit": 5}
    )
    assert resp.status_code == 200
    assert len(resp.json()["results"]["results"]) == 5


def test_recall_small_limit_sends_low_budget_and_bounded_tokens(
    hindsight_mock: SimpleNamespace,
) -> None:
    """Chat's per-turn recall (limit=8) must not trigger the engine's mid budget."""
    resp = client.get("/recall", params={"query": "q", "owner_id": "operator", "limit": 8})
    assert resp.status_code == 200
    body = json.loads(hindsight_mock.routes["recall"].calls[0].request.content)
    assert body["budget"] == "low"
    assert 512 <= body["max_tokens"] <= 1500
    assert body["max_tokens"] < 4096


def test_recall_larger_limit_sends_mid_budget(hindsight_mock: SimpleNamespace) -> None:
    resp = client.get("/recall", params={"query": "q", "owner_id": "operator", "limit": 30})
    assert resp.status_code == 200
    body = json.loads(hindsight_mock.routes["recall"].calls[0].request.content)
    assert body["budget"] == "mid"
    assert body["max_tokens"] == 4096


def test_recall_explicit_budget_wins_over_limit_heuristic(
    hindsight_mock: SimpleNamespace,
) -> None:
    resp = client.get(
        "/recall",
        params={"query": "q", "owner_id": "operator", "limit": 8, "budget": "high"},
    )
    assert resp.status_code == 200
    body = json.loads(hindsight_mock.routes["recall"].calls[0].request.content)
    assert body["budget"] == "high"


def test_recall_rejects_unknown_budget(hindsight_mock: SimpleNamespace) -> None:
    resp = client.get("/recall", params={"query": "q", "owner_id": "operator", "budget": "huge"})
    assert resp.status_code == 422
    assert not hindsight_mock.routes["recall"].called


def test_chat_skill_documents_recall_budget() -> None:
    descriptor = client.get("/chat-skill").text
    assert "budget" in descriptor
    assert "`low` | `mid` | `high`" in descriptor


def test_recall_budget_thresholds_stay_in_sync_with_constants() -> None:
    """The low/mid thresholds are documented in three prose sites — the
    /recall route Query description, the chat-skill descriptor, and the README
    /recall row. They must embed the ``hindsight_client`` constants so a
    latency re-tune fails CI until the docs are updated (single source of
    truth). ``main.py``/``chat_skill.py`` derive their text from the
    constants; the README is guarded by grepping the rendered value.
    """
    low = str(RECALL_BUDGET_LOW_MAX_LIMIT)
    mid = str(RECALL_BUDGET_MID_MAX_LIMIT)

    # /recall route Query description (via OpenAPI schema).
    budget_param = next(
        p for p in app.openapi()["paths"]["/recall"]["get"]["parameters"] if p["name"] == "budget"
    )
    route_desc = budget_param["description"]
    assert f"limit <= {low}" in route_desc
    assert f"up to {mid}" in route_desc

    # Chat-skill descriptor served at /chat-skill.
    descriptor = client.get("/chat-skill").text
    assert f"limit <= {low}" in descriptor
    assert f"up to {mid}" in descriptor

    # README /recall row.
    readme = (pathlib.Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    recall_rows = [line for line in readme.splitlines() if "`GET /recall`" in line]
    assert recall_rows, "README is missing the /recall API row"
    assert any(f"`limit <= {low}`" in row for row in recall_rows)


def test_chat_skill_contract_syncs_with_app() -> None:
    """The descriptor must stay in lockstep with the app's real contract.

    The shared ``create_chat_skill_router`` serves a markdown+frontmatter
    descriptor rather than a machine-introspectable JSON dict, so the fleet
    standard enforces sync at the *route* level: every route registered on
    the app must be documented in the descriptor and vice versa
    (``assert_chat_skill_route_parity``). ``/health/live`` and
    ``/health/hindsight`` are container/engine infra, ignored here. On top of
    parity we assert each request model's field names still appear verbatim in
    the descriptor, so a renamed/added ``RememberRequest``/``ReflectRequest``
    field fails CI until the descriptor is updated.
    """
    assert_chat_skill_route_parity(app, chat_skill(), ignore={"/health/live", "/health/hindsight"})

    descriptor = chat_skill()
    for name in RememberRequest.model_fields:
        assert name in descriptor, f"/remember field {name!r} missing from chat-skill descriptor"
    for name in ReflectRequest.model_fields:
        assert name in descriptor, f"/reflect field {name!r} missing from chat-skill descriptor"

    # update_mode's Literal members must both be advertised.
    update_annotation = RememberRequest.model_fields["update_mode"].annotation
    update_members: set[str] = set()
    for arg in get_args(update_annotation):
        if get_origin(arg) is Literal:
            update_members |= set(get_args(arg))
    assert update_members == {"append", "replace"}
    for member in update_members:
        assert member in descriptor


def test_recall_default_limit_applied(hindsight_mock: SimpleNamespace) -> None:
    engine_results = {"results": [{"text": f"fact {i}"} for i in range(20)], "entities": {}}
    hindsight_mock.routes["recall"].mock(return_value=httpx.Response(200, json=engine_results))
    resp = client.get("/recall", params={"query": "q", "owner_id": "operator"})
    assert len(resp.json()["results"]["results"]) == settings.recall_limit


def test_reflect_posts_query(hindsight_mock: SimpleNamespace) -> None:
    hindsight_mock.routes["reflect"].mock(
        return_value=httpx.Response(200, json={"answer": "reasoned answer"})
    )
    resp = client.post("/reflect", json={"query": "what matters?", "owner_id": "operator"})
    assert resp.status_code == 200
    assert resp.json()["reflection"] == {"answer": "reasoned answer"}
