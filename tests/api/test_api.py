"""Endpoint/contract tests for the robotsix-memory wrapper.

These drive the FastAPI app through ``TestClient`` and assert the
fleet-facing HTTP contract (status codes, response envelopes, error
surfacing). Hindsight is mocked at the transport layer via the shared
``hindsight_mock`` fixture; pure client request/response shaping lives
in ``tests/unit``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from robotsix_memory.main import app, settings

client = TestClient(app)


def test_health_live() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_reports_hindsight_down(hindsight_mock: SimpleNamespace) -> None:
    hindsight_mock.routes["ping"].mock(side_effect=httpx.ConnectError("refused"))
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "hindsight": "unreachable"}


def test_health_reports_hindsight_ok(hindsight_mock: SimpleNamespace) -> None:
    resp = client.get("/health")
    assert resp.json() == {"status": "ok", "hindsight": "ok"}


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
    assert "content is required" in resp.json()["detail"]


def test_remember_502_when_engine_unreachable(hindsight_mock: SimpleNamespace) -> None:
    hindsight_mock.routes["retain"].mock(side_effect=httpx.ConnectError("refused"))
    resp = client.post("/remember", json={"content": "x", "owner_id": "operator"})
    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]


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
