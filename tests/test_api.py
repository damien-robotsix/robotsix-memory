"""API tests for the robotsix-memory wrapper.

Hindsight is mocked at the transport layer with respx, with fixture
shapes mirroring the Hindsight v0.9 REST API (bank-scoped paths,
items-array retain body).
"""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from robotsix_memory.hindsight_client import bank_id
from robotsix_memory.main import app, settings

client = TestClient(app)

HS = settings.hindsight_url.rstrip("/")
OPERATOR_BANK = bank_id(settings.bank_prefix, "operator")


def test_health_live() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@respx.mock
def test_health_reports_hindsight_down() -> None:
    respx.get(f"{HS}/").mock(side_effect=httpx.ConnectError("refused"))
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "hindsight": "unreachable"}


@respx.mock
def test_health_reports_hindsight_ok() -> None:
    respx.get(f"{HS}/").mock(return_value=httpx.Response(200, json={"name": "hindsight"}))
    resp = client.get("/health")
    assert resp.json() == {"status": "ok", "hindsight": "ok"}


def test_chat_skill_shape() -> None:
    resp = client.get("/chat-skill")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["component"] == "robotsix-memory"
    paths = {e["path"] for e in doc["endpoints"]}
    assert paths == {"/remember", "/recall", "/reflect"}


@respx.mock
def test_remember_maps_to_retain() -> None:
    route = respx.post(f"{HS}/v1/default/banks/{OPERATOR_BANK}/memories/retain").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "bank_id": OPERATOR_BANK, "items_count": 1, "async": False},
        )
    )
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
    sent = route.calls[0].request
    import json as _json

    body = _json.loads(sent.content)
    assert body["async"] is False
    assert body["items"][0]["content"].startswith("Damien prefers")
    assert body["items"][0]["tags"] == ["preferences"]


@respx.mock
def test_remember_surfaces_engine_error_verbatim() -> None:
    respx.post(f"{HS}/v1/default/banks/{OPERATOR_BANK}/memories/retain").mock(
        return_value=httpx.Response(422, json={"detail": "content is required"})
    )
    resp = client.post("/remember", json={"content": "x", "owner_id": "operator"})
    assert resp.status_code == 422
    assert "content is required" in resp.json()["detail"]


@respx.mock
def test_remember_502_when_engine_unreachable() -> None:
    respx.post(f"{HS}/v1/default/banks/{OPERATOR_BANK}/memories/retain").mock(
        side_effect=httpx.ConnectError("refused")
    )
    resp = client.post("/remember", json={"content": "x", "owner_id": "operator"})
    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]


@respx.mock
def test_recall_passes_query_and_limit() -> None:
    route = respx.get(f"{HS}/v1/default/banks/{OPERATOR_BANK}/memories/recall").mock(
        return_value=httpx.Response(200, json={"memories": [{"content": "fact"}]})
    )
    resp = client.get(
        "/recall", params={"query": "preferences", "owner_id": "operator", "limit": 5}
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == {"memories": [{"content": "fact"}]}
    q = route.calls[0].request.url.params
    assert q["query"] == "preferences"
    assert q["limit"] == "5"


@respx.mock
def test_recall_default_limit_applied() -> None:
    route = respx.get(f"{HS}/v1/default/banks/{OPERATOR_BANK}/memories/recall").mock(
        return_value=httpx.Response(200, json={"memories": []})
    )
    client.get("/recall", params={"query": "q", "owner_id": "operator"})
    assert route.calls[0].request.url.params["limit"] == str(settings.recall_limit)


@respx.mock
def test_reflect_posts_query() -> None:
    respx.post(f"{HS}/v1/default/banks/{OPERATOR_BANK}/reflect").mock(
        return_value=httpx.Response(200, json={"answer": "reasoned answer"})
    )
    resp = client.post("/reflect", json={"query": "what matters?", "owner_id": "operator"})
    assert resp.status_code == 200
    assert resp.json()["reflection"] == {"answer": "reasoned answer"}


def test_owner_id_bank_sanitization() -> None:
    assert bank_id("fleet", "periodic:board-gates-drain") == "fleet-periodic-board-gates-drain"
    assert bank_id("fleet", "  ") == "fleet-default"


def test_remember_rejects_missing_owner() -> None:
    resp = client.post("/remember", json={"content": "x"})
    assert resp.status_code == 422
