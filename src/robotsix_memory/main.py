"""robotsix-memory — fleet memory component.

A thin FastAPI wrapper exposing a stable memory contract
(``/remember``, ``/recall``, ``/reflect``) backed by a Hindsight memory
engine running as a sibling container. The wrapper owns the fleet-facing
API and the chat skill; the engine stays swappable.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from typing import Annotated, Any, Literal

import structlog
from fastapi import BackgroundTasks, FastAPI, Query
from prometheus_client import start_http_server
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from robotsix_http.fastapi import (
    CorrelationIdMiddleware,
    DomainError,
    create_chat_skill_router,
    create_health_router,
    register_exception_handlers,
)

from robotsix_memory.chat_skill import chat_skill
from robotsix_memory.config import load_settings
from robotsix_memory.hindsight_client import (
    RECALL_BUDGET_LOW_MAX_LIMIT,
    RECALL_BUDGET_MID_MAX_LIMIT,
    HindsightClient,
    HindsightError,
    bank_id,
)
from robotsix_memory.logging_config import configure_logging, get_logger
from robotsix_memory.retain_replay import RetainReplayer

logger = get_logger("robotsix_memory")

settings = load_settings()


def _metrics_enabled() -> bool:
    """Whether Prometheus instrumentation is on (``ENABLE_METRICS`` env var).

    Metrics are opt-in: observability is a deploy-time concern and the
    scraped ``/metrics`` endpoint must stay behind the firewall, so it stays
    off unless a deployment explicitly sets ``ENABLE_METRICS`` to a truthy
    value (``1``/``true``/``yes``/``on``, case-insensitive).
    """
    return os.environ.get("ENABLE_METRICS", "").strip().lower() in {"1", "true", "yes", "on"}


def _package_version() -> str:
    """Report the installed distribution version (falls back for source checkouts)."""
    try:
        return _dist_version("robotsix-memory")
    except PackageNotFoundError:
        return "0.0.0"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialise structured logging before the app serves any request.

    When metrics are enabled (``ENABLE_METRICS``) a Prometheus exposition
    endpoint is started on its own port (``metrics_port``, default 8001) in a
    background thread, keeping ``/metrics`` off the fleet-facing API port so
    it can be firewalled independently.
    """
    configure_logging(settings.log_level)
    if _metrics_enabled():
        start_http_server(settings.metrics_port)
        logger.info("metrics_server_started", port=settings.metrics_port)
    logger.info("startup", version=_package_version(), hindsight_url=settings.hindsight_url)
    replay_task: asyncio.Task[None] | None = None
    if settings.retain_replay_interval > 0:
        replay_task = asyncio.create_task(replayer.run_forever())
    yield
    if replay_task is not None:
        replay_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await replay_task
    logger.info("shutdown")


app = FastAPI(title="robotsix-memory", version=_package_version(), lifespan=lifespan)

# Assign/propagate a correlation id per request and bind it (with the request
# method and path) into the structlog contextvars context so every log line —
# including the Hindsight client's — carries it across await boundaries. The
# shared robotsix-http middleware is configured with memory's header name,
# context key and request.start/end timing logs (routed through memory's own
# logger namespace).
app.add_middleware(
    CorrelationIdMiddleware,
    header_name="X-Request-ID",
    context_field="correlation_id",
    log_requests=True,
    logger=get_logger("robotsix_memory.request"),
)

# Fleet-standard exception-handler suite: request-validation, HTTPException,
# DomainError, robotsix-http's ExternalHTTPError, and a catch-all unhandled
# handler — all rendering the shared {"error": {"code", "detail"}} envelope.
register_exception_handlers(app)

# Opt-in Prometheus instrumentation: auto-capture per-request latency, volume
# and error-rate metrics via middleware. Wired before the routers so every
# route (including the health/chat-skill routers below) is measured. The
# scraped exposition endpoint is served on a separate port from the lifespan
# (see ``metrics_port``), not mounted on this app.
if _metrics_enabled():
    Instrumentator().instrument(app)

# Fleet-standard GET /chat-skill serving the markdown+frontmatter chat-access
# descriptor as text/markdown. The descriptor's frontmatter is validated at
# router-construction time and its `name` asserted against the component id.
app.include_router(create_chat_skill_router(chat_skill(), name="robotsix-memory"))

client = HindsightClient(
    settings.hindsight_url,
    timeout=settings.request_timeout,
    retain_timeout=settings.retain_timeout,
)

# Background replay of failed retain operations once the write path recovers
# (started from the lifespan; also triggerable via POST
# /admin/replay-failed-retains).
replayer = RetainReplayer(
    client,
    interval=settings.retain_replay_interval,
    per_minute=settings.retain_replay_per_minute,
)


async def _hindsight_ready() -> None:
    """Readiness check: raise unless the Hindsight engine answers."""
    if not await client.ping():
        raise RuntimeError("hindsight unreachable")


# Fleet-standard health/probe routes from the shared factory:
# GET /health -> {"status": "ok"}, GET /health/live (own-process liveness, used
# by the container health check) and GET /health/ready (engine reachability,
# each check bounded by ``check_timeout_s``; 503 ``unready`` on failure).
# /health/hindsight below is a diagnostic endpoint, not a probe.
app.include_router(
    create_health_router(
        live_path="/health/live",
        ready_path="/health/ready",
        checks={"hindsight": _hindsight_ready},
        check_timeout_s=5.0,
    )
)


class RememberRequest(BaseModel):
    content: str = Field(min_length=1, description="The fact/event to remember")
    owner_id: str = Field(min_length=1, description="Memory scope, e.g. 'operator'")
    tags: list[str] | None = None
    context: str | None = None
    timestamp: str | None = None
    document_id: str | None = None
    background: bool = Field(
        default=False,
        description=(
            "True runs the engine's fact extraction asynchronously: the call "
            "returns as soon as the item is queued (an operation id in the "
            "engine response) instead of waiting out the LLM pipeline. Use "
            "for fire-and-forget writes like rolling summaries."
        ),
    )
    update_mode: Literal["append", "replace"] | None = Field(
        default=None,
        description=(
            "With document_id: 'replace' supersedes the facts previously "
            "retained under that document (rolling-summary dedup), 'append' "
            "adds to them. Default is the engine's append behavior."
        ),
    )


class ReflectRequest(BaseModel):
    query: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)


def _domain_error_for(exc: HindsightError) -> DomainError:
    """Map a Hindsight engine failure onto the shared ``DomainError`` envelope.

    Preserves the upstream status (502 when the engine is unreachable) and
    surfaces the engine's real message under the canonical ``error`` envelope.
    """
    status = 502 if exc.status_code is None else exc.status_code
    return DomainError(str(exc), code="hindsight_error", status_code=status)


@app.get("/health/hindsight")
async def health_hindsight() -> dict[str, Any]:
    """Diagnostic status (not a probe): engine reachability plus the write path.

    Operators and the upgrade runbook read its rich payload (per-bank retain
    status, replay counters); orchestrator probes use ``/health/live`` and
    ``/health/ready`` from the shared router instead.

    ``retain`` summarises every bank's newest retain operations (see
    :meth:`HindsightClient.retain_health`); ``status`` is ``degraded`` when
    the engine is unreachable or any bank's retains are failing — the
    async extraction can fail for weeks while ``/remember`` keeps
    answering 201.
    """
    reachable = await client.ping()
    hindsight = "ok" if reachable else "unreachable"
    retain = await client.retain_health() if reachable else {"status": "unknown", "banks": {}}
    degraded = not reachable or retain.get("status") == "failing"
    retain["replay"] = replayer.state()
    return {"status": "degraded" if degraded else "ok", "hindsight": hindsight, "retain": retain}


@app.post("/admin/replay-failed-retains", status_code=202)
async def replay_failed_retains(background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Queue retries of every bank's failed retain backlog on demand.

    The wrapper has no API key, so this route relies on the internal
    network (the deploy edge does not expose it). Unlike the background
    loop it skips the write-path probe — the caller asserts the provider is
    back. Only the newest failed ``batch_retain`` per ``document_id`` is
    retried, and ids already queued in this recovery are skipped. The
    retry POSTs are sent rate-limited after the response.
    """
    planned = await replayer.plan(force=True)
    if planned:
        background_tasks.add_task(replayer.send, planned)
    return {"queued": len(planned), "replay": replayer.state()}


@app.post("/remember", status_code=201)
async def remember(body: RememberRequest) -> dict[str, Any]:
    """POST /remember: Store a durable fact in the memory bank.

    Endpoint handler binding ``owner_id`` to the request context, routing to
    the Hindsight engine's retain endpoint, and mapping engine errors to the
    standard DomainError envelope.

    Request body (RememberRequest):
        - content (str, required): Self-contained fact/event to remember.
        - owner_id (str, required): Memory scope (e.g., ``'operator'``,
          ``'periodic:preset'``).
        - tags, context, timestamp, document_id, update_mode, background:
          Optional metadata (see chat_skill docstring for details).

    Returns:
        Dict with ``'stored'`` (bool), ``'owner_id'`` (str), and ``'engine'``
        (engine response dict).

    Raises:
        DomainError: If the engine returns an error; status 502 if
            unreachable.
    """
    structlog.contextvars.bind_contextvars(owner_id=body.owner_id)
    bank = bank_id(settings.bank_prefix, body.owner_id)
    try:
        result = await client.retain(
            bank,
            body.content,
            timestamp=body.timestamp,
            context=body.context,
            tags=body.tags,
            document_id=body.document_id,
            update_mode=body.update_mode,
            background=body.background,
        )
    except HindsightError as exc:
        raise _domain_error_for(exc) from exc
    return {"stored": True, "owner_id": body.owner_id, "engine": result}


@app.get("/recall")
async def recall(
    query: Annotated[str, Query(min_length=1)],
    owner_id: Annotated[str, Query(min_length=1)],
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    tags: Annotated[list[str] | None, Query()] = None,
    budget: Annotated[
        Literal["low", "mid", "high"] | None,
        Query(
            description=(
                "Engine candidate budget. Default is derived from limit "
                f"('low' for limit <= {RECALL_BUDGET_LOW_MAX_LIMIT}, "
                f"'mid' up to {RECALL_BUDGET_MID_MAX_LIMIT}, 'high' beyond); "
                "pass explicitly to trade latency for a wider search."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Search an owner's memories via a natural-language query.

    Args:
        query: Natural-language search string to match against stored
            memories.
        owner_id: Memory scope identifier; results are restricted to this
            owner's bank.
        limit: Maximum number of results to return (1-100). Defaults to the
            configured ``recall_limit`` when omitted.
        tags: Optional topical filters that narrow results to memories
            carrying all of the given tags.
        budget: Engine candidate-pool sizing strategy ("low", "mid" or
            "high"). Defaults to a value derived from ``limit``; pass
            explicitly to trade latency for a wider search.

    Returns:
        A dict with the resolved ``owner_id`` and the ``results`` list
        returned by the recall engine.

    Raises:
        DomainError: Translated from any :class:`HindsightError` raised by
            the underlying engine via :func:`_domain_error_for`.
    """
    structlog.contextvars.bind_contextvars(owner_id=owner_id)
    bank = bank_id(settings.bank_prefix, owner_id)
    try:
        result = await client.recall(
            bank, query, limit=limit or settings.recall_limit, tags=tags, budget=budget
        )
    except HindsightError as exc:
        raise _domain_error_for(exc) from exc
    return {"owner_id": owner_id, "results": result}


@app.post("/reflect")
async def reflect(body: ReflectRequest) -> dict[str, Any]:
    """Reason over an owner's memories to answer a question.

    Unlike :func:`recall`, which performs a lookup, this endpoint asks the
    engine to synthesise an answer from the owner's stored memories.

    Args:
        body: Request payload carrying the ``query`` to reason about and the
            ``owner_id`` whose memories define the reasoning scope.

    Returns:
        A dict with the resolved ``owner_id`` and the engine's
        ``reflection`` answer.

    Raises:
        DomainError: Translated from any :class:`HindsightError` raised by
            the underlying engine via :func:`_domain_error_for`.
    """
    structlog.contextvars.bind_contextvars(owner_id=body.owner_id)
    bank = bank_id(settings.bank_prefix, body.owner_id)
    try:
        result = await client.reflect(bank, body.query)
    except HindsightError as exc:
        raise _domain_error_for(exc) from exc
    return {"owner_id": body.owner_id, "reflection": result}
