"""robotsix-memory — fleet memory component.

A thin FastAPI wrapper exposing a stable memory contract
(``/remember``, ``/recall``, ``/reflect``) backed by a Hindsight memory
engine running as a sibling container. The wrapper owns the fleet-facing
API and the chat skill; the engine stays swappable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from typing import Annotated, Any, Literal

import structlog
from fastapi import FastAPI, Query
from pydantic import BaseModel, Field
from robotsix_http.fastapi import (
    DomainError,
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
from robotsix_memory.middleware import CorrelationIdMiddleware

logger = get_logger("robotsix_memory")

settings = load_settings()


def _package_version() -> str:
    """Report the installed distribution version (falls back for source checkouts)."""
    try:
        return _dist_version("robotsix-memory")
    except PackageNotFoundError:
        return "0.0.0"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialise structured logging before the app serves any request."""
    configure_logging(settings.log_level)
    logger.info("startup", version=_package_version(), hindsight_url=settings.hindsight_url)
    yield
    logger.info("shutdown")


app = FastAPI(title="robotsix-memory", version=_package_version(), lifespan=lifespan)

# Assign/propagate a correlation id per request and bind it (with the request
# method and path) into the structlog contextvars context so every log line —
# including the Hindsight client's — carries it across await boundaries.
app.add_middleware(CorrelationIdMiddleware)

# Fleet-standard exception-handler suite: request-validation, HTTPException,
# DomainError, robotsix-http's ExternalHTTPError, and a catch-all unhandled
# handler — all rendering the shared {"error": {"code", "detail"}} envelope.
register_exception_handlers(app)

# Fleet-standard GET /health -> {"status": "ok"}. The bespoke /health/live
# (container liveness) and /health/hindsight (engine reachability) stay below.
app.include_router(create_health_router())

client = HindsightClient(
    settings.hindsight_url,
    timeout=settings.request_timeout,
    retain_timeout=settings.retain_timeout,
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


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    """Own-process liveness only — used by the container health check."""
    return {"status": "ok"}


@app.get("/health/hindsight")
async def health_hindsight() -> dict[str, str]:
    """Full status including engine reachability."""
    hindsight = "ok" if await client.ping() else "unreachable"
    return {"status": "ok", "hindsight": hindsight}


@app.get("/chat-skill")
async def get_chat_skill() -> dict[str, Any]:
    return chat_skill()


@app.post("/remember", status_code=201)
async def remember(body: RememberRequest) -> dict[str, Any]:
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
    structlog.contextvars.bind_contextvars(owner_id=body.owner_id)
    bank = bank_id(settings.bank_prefix, body.owner_id)
    try:
        result = await client.reflect(bank, body.query)
    except HindsightError as exc:
        raise _domain_error_for(exc) from exc
    return {"owner_id": body.owner_id, "reflection": result}
