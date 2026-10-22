"""Unit tests for the failed-retain replay loop (``RetainReplayer``).

The ``operations`` route mirrors the live Hindsight shape (``id``,
``document_id``, ``task_type``, ``status``, ``created_at``,
``retry_count``) observed while replaying the 2026-09/10 backlog by hand.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from robotsix_memory.hindsight_client import HindsightClient
from robotsix_memory.retain_replay import RetainReplayer, select_replay_ops


def _op(
    op_id: str,
    status: str,
    created_at: str,
    *,
    kind: str = "batch_retain",
    document_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": op_id,
        "document_id": document_id,
        "task_type": kind,
        "status": status,
        "created_at": created_at,
        "retry_count": 3 if status == "failed" else 0,
    }


FAILED_BACKLOG = [
    _op("op-a1", "failed", "2026-10-01T08:00:00+00:00", document_id="doc-a"),
    _op("op-b1", "failed", "2026-10-02T08:00:00+00:00", document_id="doc-b"),
    _op("op-a2", "failed", "2026-10-03T08:00:00+00:00", document_id="doc-a"),
]
RECOVERED = _op("op-ok", "completed", "2026-10-22T09:00:00+00:00", kind="retain")


async def _no_sleep(_seconds: float) -> None:
    return None


def _replayer(client: HindsightClient) -> RetainReplayer:
    return RetainReplayer(client, interval=900.0, per_minute=30, sleep=_no_sleep)


def _retried_ids(hindsight_mock: SimpleNamespace) -> list[str]:
    return [call.request.url.path.split("/")[-2] for call in hindsight_mock.routes["retry"].calls]


def test_select_replay_ops_keeps_newest_failed_per_document() -> None:
    ops = [*FAILED_BACKLOG, RECOVERED, _op("op-x", "failed", "2026-10-04T00:00:00+00:00")]
    assert select_replay_ops(ops) == ["op-b1", "op-a2", "op-x"]


async def test_replay_retries_newest_per_document_once(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    hindsight_mock.routes["operations"].mock(
        return_value=httpx.Response(200, json={"operations": [RECOVERED, *FAILED_BACKLOG]})
    )
    replayer = _replayer(hs_client)

    assert await replayer.tick() == 2
    assert sorted(_retried_ids(hindsight_mock)) == ["op-a2", "op-b1"]

    # A second tick sees the same failed ops but queues nothing new.
    assert await replayer.tick() == 0
    assert hindsight_mock.routes["retry"].call_count == 2
    state = replayer.state()
    assert state["queued"] == 2
    assert state["last_at"] is not None


async def test_replay_waits_for_write_path_to_recover(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    ops_route = hindsight_mock.routes["operations"]
    ops_route.mock(return_value=httpx.Response(200, json={"operations": FAILED_BACKLOG}))
    replayer = _replayer(hs_client)

    # Bank failing, no completed retain yet: the probe fails, nothing queued.
    assert await replayer.tick() == 0
    assert hindsight_mock.routes["retry"].call_count == 0
    assert replayer.state()["pending_banks"] == [hindsight_mock.bank]

    # The newest retain just succeeded: the backlog is replayed.
    ops_route.mock(
        return_value=httpx.Response(200, json={"operations": [RECOVERED, *FAILED_BACKLOG]})
    )
    assert await replayer.tick() == 2
    assert replayer.state()["pending_banks"] == []


async def test_forced_replay_skips_probe(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    hindsight_mock.routes["operations"].mock(
        return_value=httpx.Response(200, json={"operations": FAILED_BACKLOG})
    )
    replayer = _replayer(hs_client)
    assert await replayer.tick(force=True) == 2
    assert sorted(_retried_ids(hindsight_mock)) == ["op-a2", "op-b1"]


async def test_send_rate_limits_and_survives_retry_errors(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    sleeps: list[float] = []

    async def _record(seconds: float) -> None:
        sleeps.append(seconds)

    hindsight_mock.routes["retry"].mock(
        side_effect=[httpx.Response(404, json={"detail": "gone"}), httpx.Response(200, json={})]
    )
    replayer = RetainReplayer(hs_client, interval=900.0, per_minute=30, sleep=_record)
    sent = await replayer.send([(hindsight_mock.bank, "op-1"), (hindsight_mock.bank, "op-2")])
    assert sent == 1
    assert sleeps == [2.0]
