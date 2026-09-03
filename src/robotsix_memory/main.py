"""robotsix-memory — fleet memory component.

A thin FastAPI wrapper exposing a stable memory contract
(``/remember``, ``/recall``, ``/reflect``) backed by a Hindsight memory
engine running as a sibling container. The wrapper owns the fleet-facing
API and the chat skill; the engine stays swappable.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from robotsix_memory.chat_skill import chat_skill
from robotsix_memory.config import load_settings
from robotsix_memory.hindsight_client import HindsightClient, HindsightError, bank_id

logger = logging.getLogger("robotsix_memory")

settings = load_settings()
logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(title="robotsix-memory", version="0.1.0")

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


def _raise_for(exc: HindsightError) -> HTTPException:
    status = 502 if exc.status_code is None else exc.status_code
    return HTTPException(status_code=status, detail=str(exc))


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    """Own-process liveness only — used by the container health check."""
    return {"status": "ok"}


@app.get("/health")
async def health() -> dict[str, str]:
    """Full status including engine reachability."""
    hindsight = "ok" if await client.ping() else "unreachable"
    return {"status": "ok", "hindsight": hindsight}


@app.get("/chat-skill")
async def get_chat_skill() -> dict[str, Any]:
    return chat_skill()


@app.post("/remember", status_code=201)
async def remember(body: RememberRequest) -> dict[str, Any]:
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
        )
    except HindsightError as exc:
        raise _raise_for(exc) from exc
    return {"stored": True, "owner_id": body.owner_id, "engine": result}


@app.get("/recall")
async def recall(
    query: Annotated[str, Query(min_length=1)],
    owner_id: Annotated[str, Query(min_length=1)],
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    tags: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    bank = bank_id(settings.bank_prefix, owner_id)
    try:
        result = await client.recall(bank, query, limit=limit or settings.recall_limit, tags=tags)
    except HindsightError as exc:
        raise _raise_for(exc) from exc
    return {"owner_id": owner_id, "results": result}


@app.post("/reflect")
async def reflect(body: ReflectRequest) -> dict[str, Any]:
    bank = bank_id(settings.bank_prefix, body.owner_id)
    try:
        result = await client.reflect(bank, body.query)
    except HindsightError as exc:
        raise _raise_for(exc) from exc
    return {"owner_id": body.owner_id, "reflection": result}
