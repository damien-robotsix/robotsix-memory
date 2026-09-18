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
    resp = client.get("/chat-skill")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["component"] == "robotsix-memory"
    paths = {e["path"] for e in doc["endpoints"]}
    assert paths == {"/remember", "/recall", "/reflect"}


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
    doc = client.get("/chat-skill").json()
    recall_entry = next(e for e in doc["endpoints"] if e["path"] == "/recall")
    assert "budget" in recall_entry["params"]


def test_recall_budget_thresholds_stay_in_sync_with_constants() -> None:
    """The low/mid thresholds are documented in three prose sites — the
    /recall route Query description, the chat-skill doc, and the README
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

    # Chat-skill doc served at /chat-skill.
    doc = client.get("/chat-skill").json()
    recall_entry = next(e for e in doc["endpoints"] if e["path"] == "/recall")
    budget_doc = recall_entry["params"]["budget"]
    assert f"limit <= {low}" in budget_doc
    assert f"up to {mid}" in budget_doc

    # README /recall row.
    readme = (pathlib.Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    recall_rows = [line for line in readme.splitlines() if "`GET /recall`" in line]
    assert recall_rows, "README is missing the /recall API row"
    assert any(f"`limit <= {low}`" in row for row in recall_rows)


def test_chat_skill_contract_syncs_with_app() -> None:
    """The skill doc must stay in lockstep with the app's real contract.

    chat_skill() hand-writes /remember, /recall and /reflect, so a model
    field renamed or added, a default changed (limit, background,
    update_mode's Literal), or a route added would silently desync the
    chat agents that build calls from this document. Regenerate each entry
    from the app's actual models/routes and fail CI on any drift:
      * /remember body == RememberRequest.model_fields
      * /recall params == the /recall route's Query-declared params
      * /reflect body  == ReflectRequest.model_fields
    """
    doc = client.get("/chat-skill").json()
    endpoints = {e["path"]: e for e in doc["endpoints"]}
    assert set(endpoints) == {"/remember", "/recall", "/reflect"}

    # /remember body mirrors RememberRequest (fields, requiredness, defaults).
    remember = endpoints["/remember"]["body"]
    assert set(remember) == set(RememberRequest.model_fields)
    required_remember = {
        name for name, field in RememberRequest.model_fields.items() if field.is_required()
    }
    assert {name for name, desc in remember.items() if "(required)" in desc} == required_remember
    assert RememberRequest.model_fields["background"].default is False
    assert "default false" in remember["background"]
    update_annotation = RememberRequest.model_fields["update_mode"].annotation
    update_members: set[str] = set()
    for arg in get_args(update_annotation):
        if get_origin(arg) is Literal:
            update_members |= set(get_args(arg))
    assert update_members == {"append", "replace"}

    # /reflect body mirrors ReflectRequest (fields, requiredness).
    reflect = endpoints["/reflect"]["body"]
    assert set(reflect) == set(ReflectRequest.model_fields)
    required_reflect = {
        name for name, field in ReflectRequest.model_fields.items() if field.is_required()
    }
    assert {name for name, desc in reflect.items() if "(required)" in desc} == required_reflect

    # /recall params mirror the route's Query-declared params (names,
    # requiredness, and the effective limit default).
    recall_params = endpoints["/recall"]["params"]
    spec_params = {p["name"]: p for p in app.openapi()["paths"]["/recall"]["get"]["parameters"]}
    assert set(recall_params) == set(spec_params)
    documented_required = {name for name, desc in recall_params.items() if "(required)" in desc}
    assert documented_required == {name for name, p in spec_params.items() if p.get("required")}
    # The route's limit default is None; the effective one the doc promises
    # is settings.recall_limit (e.g. "optional int (default 10)").
    assert f"default {settings.recall_limit}" in recall_params["limit"]


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
