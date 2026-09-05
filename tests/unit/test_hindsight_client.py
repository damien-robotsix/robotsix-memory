"""Pure-logic tests for ``HindsightClient`` request/response shaping.

No FastAPI ``TestClient`` here — the client is driven directly and the
request bodies it emits (plus the response slicing it applies) are
asserted against the shared ``hindsight_mock`` respx routes.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from robotsix_memory.hindsight_client import HindsightClient, HindsightError, bank_id


def test_bank_id_sanitizes_owner() -> None:
    assert bank_id("fleet", "periodic:board-gates-drain") == "fleet-periodic-board-gates-drain"


def test_bank_id_blank_owner_falls_back_to_default() -> None:
    assert bank_id("fleet", "  ") == "fleet-default"


async def test_retain_shapes_items_and_sync_flag(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    await hs_client.retain(
        hindsight_mock.bank,
        "Damien prefers merge-now over approve on MR gates.",
        tags=["preferences"],
    )
    body = json.loads(hindsight_mock.routes["retain"].calls[0].request.content)
    assert body["async"] is False
    assert body["items"][0]["content"].startswith("Damien prefers")
    assert body["items"][0]["tags"] == ["preferences"]


async def test_retain_maps_replace_update_mode_and_document_id(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    await hs_client.retain(
        hindsight_mock.bank,
        "Rolling summary of session abc.",
        document_id="chat-session-abc",
        update_mode="replace",
    )
    body = json.loads(hindsight_mock.routes["retain"].calls[0].request.content)
    assert body["items"][0]["document_id"] == "chat-session-abc"
    assert body["items"][0]["update_mode"] == "replace"


async def test_retain_background_sets_async_flag(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    await hs_client.retain(hindsight_mock.bank, "slow summary", background=True)
    body = json.loads(hindsight_mock.routes["retain"].calls[0].request.content)
    assert body["async"] is True


async def test_recall_posts_query_and_slices_to_limit(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    engine_results = {"results": [{"text": f"fact {i}"} for i in range(20)], "entities": {}}
    hindsight_mock.routes["recall"].mock(return_value=httpx.Response(200, json=engine_results))
    result = await hs_client.recall(hindsight_mock.bank, "preferences", limit=5)
    assert len(result["results"]) == 5
    body = json.loads(hindsight_mock.routes["recall"].calls[0].request.content)
    assert body["query"] == "preferences"


async def test_reflect_posts_query(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    hindsight_mock.routes["reflect"].mock(
        return_value=httpx.Response(200, json={"answer": "reasoned answer"})
    )
    result = await hs_client.reflect(hindsight_mock.bank, "what matters?")
    assert result == {"answer": "reasoned answer"}
    body = json.loads(hindsight_mock.routes["reflect"].calls[0].request.content)
    assert body["query"] == "what matters?"


async def test_request_retries_transient_transport_error(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    """A pre-delivery transport failure on a POST is retried by RetryClient."""
    hindsight_mock.routes["recall"].mock(
        side_effect=[
            httpx.ConnectError("connection refused"),
            httpx.Response(200, json={"results": [{"text": "fact"}], "entities": {}}),
        ]
    )
    result = await hs_client.recall(hindsight_mock.bank, "preferences", limit=5)
    assert result["results"] == [{"text": "fact"}]
    assert hindsight_mock.routes["recall"].call_count == 2


async def test_request_maps_engine_error_to_hindsight_error(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    """A 5xx from the engine surfaces as ``HindsightError`` with the status."""
    hindsight_mock.routes["retain"].mock(return_value=httpx.Response(503, text="down"))
    with pytest.raises(HindsightError) as excinfo:
        await hs_client.retain(hindsight_mock.bank, "note")
    assert excinfo.value.status_code == 503


async def test_ping_retries_then_succeeds(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    """``ping`` retries a transient 5xx (a safe GET) before answering True."""
    hindsight_mock.routes["ping"].mock(
        side_effect=[
            httpx.Response(503, json={}),
            httpx.Response(200, json={"name": "hindsight"}),
        ]
    )
    assert await hs_client.ping() is True


async def test_ping_false_on_persistent_server_error(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    hindsight_mock.routes["ping"].mock(return_value=httpx.Response(503, json={}))
    assert await hs_client.ping() is False
