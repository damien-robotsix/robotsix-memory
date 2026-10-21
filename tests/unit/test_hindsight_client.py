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
import structlog

from robotsix_memory.hindsight_client import (
    RECALL_QUERY_SHRINK_ATTEMPTS,
    RETAIN_HEALTH_FAILING_AFTER,
    HindsightClient,
    HindsightError,
    bank_id,
    recall_budget_for,
    recall_max_tokens_for,
    shrink_recall_query,
)


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


@pytest.mark.parametrize(
    ("limit", "budget", "max_tokens"),
    [
        (1, "low", 512),  # clamped up: a single result still gets a floor
        (8, "low", 1280),  # chat's per-turn recall: 8 * 160
        (10, "low", 1600),
        (11, "mid", 1760),
        (30, "mid", 4096),  # 30 * 160 = 4800 clamped to the engine ceiling
        (50, "mid", 4096),
        (51, "high", 4096),
    ],
)
def test_recall_sizing_heuristic(limit: int, budget: str, max_tokens: int) -> None:
    assert recall_budget_for(limit) == budget
    assert recall_max_tokens_for(limit) == max_tokens


async def test_recall_sizes_engine_work_to_limit(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    await hs_client.recall(hindsight_mock.bank, "preferences", limit=8)
    body = json.loads(hindsight_mock.routes["recall"].calls[0].request.content)
    assert body == {"query": "preferences", "budget": "low", "max_tokens": 1280}


async def test_recall_explicit_budget_overrides_heuristic(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    await hs_client.recall(hindsight_mock.bank, "preferences", limit=8, budget="high")
    body = json.loads(hindsight_mock.routes["recall"].calls[0].request.content)
    assert body["budget"] == "high"
    assert body["max_tokens"] == 1280


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


async def test_request_logs_method_url_and_status(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    """A successful request emits one structured completion log line."""
    hindsight_mock.routes["reflect"].mock(return_value=httpx.Response(200, json={"answer": "ok"}))
    with structlog.testing.capture_logs() as logs:
        await hs_client.reflect(hindsight_mock.bank, "q")
    completed = [entry for entry in logs if entry["event"] == "hindsight.request.complete"]
    assert len(completed) == 1
    assert completed[0]["method"] == "POST"
    assert completed[0]["url"].endswith("/reflect")
    assert completed[0]["status_code"] == 200
    assert "duration_ms" in completed[0]


async def test_request_logs_error_status(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    """A 5xx from the engine is logged with its status before raising."""
    hindsight_mock.routes["retain"].mock(return_value=httpx.Response(503, text="down"))
    with structlog.testing.capture_logs() as logs:  # noqa: SIM117
        with pytest.raises(HindsightError):
            await hs_client.retain(hindsight_mock.bank, "note")
    errors = [entry for entry in logs if entry["event"] == "hindsight.request.error"]
    assert errors
    assert errors[0]["status_code"] == 503


_TOO_LONG_DETAIL = {
    "detail": "Query too long: 751 tokens exceeds maximum of 500. Please shorten your query."
}


async def test_recall_shrinks_query_on_too_long_400_and_retries(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    """A 400 'Query too long' shrinks the query by the engine's ratio and retries.

    Mirrors the live 2026-10-21 shape: chat sent a ~2000-char decision-panel
    prompt, hindsight 0.9.2 answered ``400 {"detail": "Query too long: 751
    tokens exceeds maximum of 500 …"}`` and the wrapper surfaced it.
    """
    query = " ".join(f"20261006T163511Z-ticket-{i:04d}" for i in range(80))
    hindsight_mock.routes["recall"].mock(
        side_effect=[
            httpx.Response(400, json=_TOO_LONG_DETAIL),
            httpx.Response(200, json={"results": [{"id": "m1", "text": "fact"}]}),
        ]
    )
    result = await hs_client.recall(hindsight_mock.bank, query, limit=8)
    assert result["results"] == [{"id": "m1", "text": "fact"}]
    assert hindsight_mock.routes["recall"].call_count == 2
    first = json.loads(hindsight_mock.routes["recall"].calls[0].request.content)["query"]
    second = json.loads(hindsight_mock.routes["recall"].calls[1].request.content)["query"]
    assert first == query
    assert 0 < len(second) < len(first)
    # 500 * 0.9 / 751 of the original, cut on a word boundary
    assert len(second) <= int(len(query) * 450 / 751)
    assert query.startswith(second)
    assert not second.endswith("-")


async def test_recall_gives_up_after_shrink_attempts(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    hindsight_mock.routes["recall"].mock(return_value=httpx.Response(400, json=_TOO_LONG_DETAIL))
    with pytest.raises(HindsightError) as excinfo:
        await hs_client.recall(hindsight_mock.bank, "word " * 400, limit=8)
    assert excinfo.value.status_code == 400
    assert hindsight_mock.routes["recall"].call_count == RECALL_QUERY_SHRINK_ATTEMPTS + 1


async def test_recall_other_400_is_not_retried(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    hindsight_mock.routes["recall"].mock(
        return_value=httpx.Response(400, json={"detail": "budget must be one of low/mid/high"})
    )
    with pytest.raises(HindsightError):
        await hs_client.recall(hindsight_mock.bank, "preferences", limit=8)
    assert hindsight_mock.routes["recall"].call_count == 1


@pytest.mark.parametrize(
    ("query", "detail", "expect_max"),
    [
        ("a" * 1000, "Query too long: 1000 tokens exceeds maximum of 500.", 450),
        ("word " * 200, "unparseable detail", 500),
        ("ab", "Query too long: 2 tokens exceeds maximum of 1.", 1),
    ],
)
def test_shrink_recall_query_bounds(query: str, detail: str, expect_max: int) -> None:
    shrunk = shrink_recall_query(query, detail)
    assert 1 <= len(shrunk) <= expect_max
    assert query.startswith(shrunk)


def _op(status: str, kind: str = "retain", error: str | None = None) -> dict[str, object]:
    return {
        "operation_id": None,
        "status": status,
        "task_type": kind,
        "created_at": "2026-10-21T09:43:21+00:00",
        "error_message": error,
    }


async def test_retain_health_ok_when_recent_ops_succeed(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    hindsight_mock.routes["operations"].mock(
        return_value=httpx.Response(
            200, json={"operations": [_op("completed"), _op("failed", error="x"), _op("completed")]}
        )
    )
    health = await hs_client.retain_health()
    assert health["status"] == "ok"
    bank = health["banks"][hindsight_mock.bank]
    assert bank == {
        "status": "ok",
        "last_write_at": "2026-10-21T08:00:00+00:00",
        "failed": 1,
        "checked": 3,
        "last_error": "x",
        "last_failed_at": "2026-10-21T09:43:21+00:00",
    }


async def test_retain_health_failing_needs_consecutive_failures_and_no_success(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    ops = [_op("failed", "batch_retain", "requires more credits")] * RETAIN_HEALTH_FAILING_AFTER
    # a non-retain task type must not count
    ops.append(_op("completed", "consolidation"))
    hindsight_mock.routes["operations"].mock(
        return_value=httpx.Response(200, json={"operations": ops})
    )
    health = await hs_client.retain_health()
    assert health["status"] == "failing"
    assert health["last_error"] == "requires more credits"
    assert health["banks"][hindsight_mock.bank]["checked"] == RETAIN_HEALTH_FAILING_AFTER


async def test_retain_health_unknown_when_bank_list_fails(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    hindsight_mock.routes["banks"].mock(return_value=httpx.Response(500, json={"detail": "boom"}))
    health = await hs_client.retain_health()
    assert health["status"] == "unknown"
    assert health["banks"] == {}


async def test_retain_health_marks_bank_unknown_when_operations_fail(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    hindsight_mock.routes["operations"].mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )
    health = await hs_client.retain_health()
    assert health["status"] == "ok"
    assert health["banks"][hindsight_mock.bank]["status"] == "unknown"
