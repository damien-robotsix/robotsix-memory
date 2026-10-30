"""Replay failed Hindsight retain operations once the write path recovers.

Hindsight retries a failed ``retain``/``batch_retain`` task only three
times one minute apart; after that the operation stays ``failed`` forever.
When the extraction LLM provider was down for a month (2026-09-24 →
2026-10-22) nothing re-attempted the backlog once the provider came back —
the operator had to retry 190 operations by hand. :class:`RetainReplayer`
automates that recovery:

1. every tick it reads :meth:`HindsightClient.retain_health`; a bank that
   reports ``failing`` (or still carries recent failures) becomes a replay
   candidate;
2. it probes the write path by watching for the first completed retain:
   once ANY bank's newest retain operation completed, the (shared) provider
   is answering again;
3. it then lists each candidate bank's failed ``batch_retain`` operations,
   keeps only the NEWEST failed op per ``document_id`` (document pushes use
   ``update_mode=replace``, so older versions are superseded) and queues
   ``POST …/operations/{id}/retry`` for each, rate-limited and logged as
   ``retain.replay``.

An operation id is retried at most once per recovery episode: queued ids
are remembered per bank and only forgotten when that bank starts failing
again. The state is exposed on ``/health/hindsight`` as ``retain.replay``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from robotsix_memory.hindsight_client import HindsightClient, HindsightError
from robotsix_memory.logging_config import get_logger

logger = get_logger("robotsix_memory.retain_replay")

_REPLAY_TASK_TYPE = "batch_retain"
_RETAIN_TASK_TYPES = frozenset({"retain", "batch_retain"})
_SUCCESS_STATUSES = frozenset({"completed", "success"})


def _op_id(op: dict[str, Any]) -> str | None:
    """The operation id (live shape ``id``; older payloads ``operation_id``)."""
    value = op.get("id") or op.get("operation_id")
    return str(value) if value else None


def _created_key(op: dict[str, Any]) -> datetime:
    """Sort key: ``created_at`` as an aware datetime (unparseable sorts oldest)."""
    raw = op.get("created_at")
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            pass
        else:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def newest_retain_succeeded(ops: list[dict[str, Any]]) -> bool:
    """True when the newest ``retain``/``batch_retain`` operation completed."""
    retains = [op for op in ops if op.get("task_type") in _RETAIN_TASK_TYPES]
    if not retains:
        return False
    newest = max(retains, key=_created_key)
    return newest.get("status") in _SUCCESS_STATUSES


def select_replay_ops(ops: list[dict[str, Any]]) -> list[str]:
    """Ids of the failed ``batch_retain`` ops to retry: the newest per document.

    Operations without a ``document_id`` are independent appends and are
    each kept. The result is ordered oldest first so replays land in the
    original write order.
    """
    failed = [
        op
        for op in ops
        if op.get("task_type") == _REPLAY_TASK_TYPE and op.get("status") == "failed" and _op_id(op)
    ]
    newest: dict[str, dict[str, Any]] = {}
    for op in failed:
        key = str(op.get("document_id") or f"op:{_op_id(op)}")
        current = newest.get(key)
        if current is None or _created_key(op) > _created_key(current):
            newest[key] = op
    chosen = sorted(newest.values(), key=_created_key)
    return [op_id for op in chosen if (op_id := _op_id(op))]


class RetainReplayer:
    """Detects a recovered write path and replays the failed retain backlog."""

    def __init__(
        self,
        client: HindsightClient,
        *,
        interval: float,
        per_minute: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._interval = interval
        self._per_minute = max(1, per_minute)
        self._sleep = sleep
        # Banks seen failing whose backlog has not been replayed yet.
        self._pending: set[str] = set()
        # Banks whose last observed retain status was ``failing``.
        self._failing: set[str] = set()
        # Op ids queued for retry in the current recovery episode, per bank.
        self._queued: dict[str, set[str]] = {}
        self._queued_total = 0
        self._last_at: str | None = None

    def state(self) -> dict[str, Any]:
        """Replay counters for ``/health/hindsight`` (``retain.replay``)."""
        return {
            "queued": self._queued_total,
            "last_at": self._last_at,
            "pending_banks": sorted(self._pending),
        }

    async def plan(self, *, force: bool = False) -> list[tuple[str, str]]:
        """Pick the ``(bank, op_id)`` retries to queue now and mark them queued.

        Without ``force`` nothing is planned until the write path is proven
        healthy (some bank's newest retain completed). ``force`` skips that
        probe and considers every bank with recent failures — the operator
        asserts the provider is back.
        """
        health = await self._client.retain_health()
        candidates = self._absorb_health(health.get("banks", {}))
        if not candidates:
            return []

        ops_by_bank: dict[str, list[dict[str, Any]]] = {}
        for name in sorted(candidates):
            try:
                ops_by_bank[name] = await self._client.list_operations(name)
            except HindsightError as exc:
                logger.warning("retain.replay.list_failed", bank=name, error=str(exc)[:300])
        if not force and not any(newest_retain_succeeded(ops) for ops in ops_by_bank.values()):
            return []
        return self._record_planned(ops_by_bank)

    def _absorb_health(self, banks: dict[str, Any]) -> set[str]:
        """Fold a ``retain_health()["banks"]`` sweep into the state; return candidates.

        A bank turning ``failing`` after being healthy starts a new failure
        episode (its queued op ids are forgotten); an ``ok`` status clears the
        failing mark. Banks with recent ``failed`` entries or a pending
        backlog are replay candidates.
        """
        candidates: set[str] = set()
        for name, entry in banks.items():
            status = entry.get("status")
            if status == "failing":
                self._mark_failing(name)
            elif status == "ok":
                self._failing.discard(name)
            if entry.get("failed"):
                candidates.add(name)
        return candidates | (self._pending & set(banks))

    def _mark_failing(self, name: str) -> None:
        """Record ``name`` as failing, opening a new episode if it was healthy."""
        if name not in self._failing:
            # A new failure episode (the bank was healthy when last
            # seen): its ops may be retried again in this recovery.
            self._queued.pop(name, None)
            self._failing.add(name)
        self._pending.add(name)

    def _record_planned(
        self, ops_by_bank: dict[str, list[dict[str, Any]]]
    ) -> list[tuple[str, str]]:
        """De-duplicate replay ops against this episode's queue and record them."""
        planned: list[tuple[str, str]] = []
        for name, ops in ops_by_bank.items():
            seen = self._queued.setdefault(name, set())
            fresh = [op_id for op_id in select_replay_ops(ops) if op_id not in seen]
            seen.update(fresh)
            self._pending.discard(name)
            if fresh:
                logger.info("retain.replay", queued=len(fresh), bank=name)
                planned.extend((name, op_id) for op_id in fresh)
        if planned:
            self._queued_total += len(planned)
            self._last_at = datetime.now(UTC).isoformat()
        return planned

    async def send(self, planned: list[tuple[str, str]]) -> int:
        """POST the retries, at most ``per_minute`` per minute; return successes."""
        delay = 60.0 / self._per_minute
        sent = 0
        for index, (bank, op_id) in enumerate(planned):
            if index:
                await self._sleep(delay)
            try:
                await self._client.retry_operation(bank, op_id)
            except HindsightError as exc:
                logger.warning(
                    "retain.replay.retry_failed", bank=bank, operation_id=op_id, error=str(exc)
                )
                continue
            sent += 1
        return sent

    async def tick(self, *, force: bool = False) -> int:
        """One loop iteration: plan the replays, then send them."""
        planned = await self.plan(force=force)
        if planned:
            await self.send(planned)
        return len(planned)

    async def run_forever(self) -> None:
        """Background loop: one :meth:`tick` every ``interval`` seconds."""
        while True:
            await self._sleep(self._interval)
            try:
                await self.tick()
            except Exception:  # never let one bad tick kill the loop
                logger.exception("retain.replay.tick_failed")
