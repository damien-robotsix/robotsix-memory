"""Unit tests for the failed-retain replay loop (``RetainReplayer``).

The ``operations`` route mirrors the live Hindsight shape (``id``,
``document_id``, ``task_type``, ``status``, ``created_at``,
``retry_count``) observed while replaying the 2026-09/10 backlog by hand.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
import structlog

from robotsix_memory.hindsight_client import HindsightClient, HindsightError
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


class _FakeClient:
    """Scripted stand-in for :class:`HindsightClient`.

    ``health`` is consumed one entry per ``retain_health`` call (the last
    entry repeats); an entry that is an exception is raised instead.
    ``ops`` maps bank -> operations list, or an exception to raise.
    """

    def __init__(
        self,
        health: list[dict[str, Any] | Exception],
        ops: dict[str, list[dict[str, Any]] | Exception],
    ) -> None:
        self._health = health
        self.ops = ops
        self.health_calls = 0
        self.retried: list[tuple[str, str]] = []

    async def retain_health(self) -> dict[str, Any]:
        entry = self._health[min(self.health_calls, len(self._health) - 1)]
        self.health_calls += 1
        if isinstance(entry, Exception):
            raise entry
        return entry

    async def list_operations(self, bank: str) -> list[dict[str, Any]]:
        result = self.ops[bank]
        if isinstance(result, Exception):
            raise result
        return result

    async def retry_operation(self, bank: str, op_id: str) -> dict[str, Any]:
        self.retried.append((bank, op_id))
        return {}


def _health(**statuses: str) -> dict[str, Any]:
    return {"banks": {name: {"status": status} for name, status in statuses.items()}}


def _fake_replayer(
    fake: _FakeClient, sleep: Callable[[float], Awaitable[None]] = _no_sleep
) -> RetainReplayer:
    return RetainReplayer(cast(HindsightClient, fake), interval=900.0, per_minute=30, sleep=sleep)


async def test_new_failure_episode_allows_ops_to_be_retried_again() -> None:
    fake = _FakeClient(
        health=[
            _health(bank="failing"),
            _health(bank="failing"),
            _health(bank="ok"),
            _health(bank="failing"),
        ],
        ops={"bank": [RECOVERED, *FAILED_BACKLOG]},
    )
    replayer = _fake_replayer(fake)

    # First outage: the backlog is replayed once.
    assert await replayer.tick() == 2
    # Still the same failure episode: queued ids are remembered, nothing re-sent.
    assert await replayer.tick() == 0
    # The bank recovers.
    assert await replayer.tick() == 0
    # A second outage starts a new episode: the same op ids are retried again.
    assert await replayer.tick() == 2

    assert sorted(fake.retried) == [
        ("bank", "op-a2"),
        ("bank", "op-a2"),
        ("bank", "op-b1"),
        ("bank", "op-b1"),
    ]
    assert replayer.state()["queued"] == 4


async def test_list_operations_error_skips_bank_and_plans_others() -> None:
    fake = _FakeClient(
        health=[_health(broken="failing", good="failing")],
        ops={
            "broken": HindsightError("boom", status_code=500),
            "good": [RECOVERED, *FAILED_BACKLOG],
        },
    )
    replayer = _fake_replayer(fake)

    with structlog.testing.capture_logs() as logs:
        assert await replayer.tick() == 2

    assert sorted(fake.retried) == [("good", "op-a2"), ("good", "op-b1")]
    failures = [log for log in logs if log["event"] == "retain.replay.list_failed"]
    assert len(failures) == 1
    assert failures[0]["bank"] == "broken"
    assert failures[0]["log_level"] == "warning"
    # The skipped bank's backlog is still owed a replay.
    assert replayer.state()["pending_banks"] == ["broken"]


async def test_run_forever_survives_failing_ticks() -> None:
    fake = _FakeClient(health=[RuntimeError("hindsight exploded")], ops={})
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 3:
            raise asyncio.CancelledError

    replayer = _fake_replayer(fake, sleep=_sleep)

    with structlog.testing.capture_logs() as logs, pytest.raises(asyncio.CancelledError):
        await replayer.run_forever()

    # Every failing tick was logged and the loop kept going until cancelled.
    assert fake.health_calls == 3
    assert [log["event"] for log in logs] == ["retain.replay.tick_failed"] * 3
    assert sleeps == [900.0] * 4


async def test_pending_banks_tracks_unreplayed_backlog() -> None:
    fake = _FakeClient(
        health=[_health(bank="failing"), _health(bank="ok"), _health(bank="ok")],
        ops={"bank": FAILED_BACKLOG},
    )
    replayer = _fake_replayer(fake)
    assert replayer.state()["pending_banks"] == []

    # Failing, write path unproven: the bank becomes pending.
    assert await replayer.tick() == 0
    assert replayer.state()["pending_banks"] == ["bank"]

    # The bank reports ok but no retain has completed yet: still pending.
    assert await replayer.tick() == 0
    assert replayer.state()["pending_banks"] == ["bank"]

    # The newest retain completed: the backlog is planned and pending cleared.
    fake.ops["bank"] = [RECOVERED, *FAILED_BACKLOG]
    assert await replayer.tick() == 2
    assert replayer.state()["pending_banks"] == []
