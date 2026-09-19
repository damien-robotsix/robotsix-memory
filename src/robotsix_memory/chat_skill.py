"""Chat-agent skill document for the robotsix-memory component.

Served at ``GET /chat-skill`` (via ``robotsix_http.fastapi.create_chat_skill_router``)
and read by chat agents via the deploy roster. It follows the fleet
markdown+frontmatter chat-access standard: a ``---``-delimited YAML block
declaring a kebab-case ``name`` and a one-sentence ``description``, followed by
a markdown body documenting the fleet-facing contract. Hindsight is an
implementation detail and its native API is deliberately not advertised.
"""

from __future__ import annotations

from robotsix_memory.hindsight_client import (
    RECALL_BUDGET_LOW_MAX_LIMIT,
    RECALL_BUDGET_MID_MAX_LIMIT,
)


def chat_skill() -> str:
    """Return the markdown+frontmatter chat-agent skill descriptor."""
    return f"""\
---
name: robotsix-memory
description: Long-term fleet memory: store, recall, and reason over durable facts per owner_id.
---

# robotsix-memory

Long-term fleet memory. Store durable facts, preferences, and outcomes with
`/remember`; retrieve them with `/recall`; ask the memory bank to reason over
what it knows with `/reflect`. Memories are consolidated in the background into
deduplicated, evidence-grounded observations per owner, so remember freely —
repeats merge instead of piling up.

## Base

- Port 8080.
- Health: `GET /health` -> `{{"status": "ok"}}`; `GET /health/hindsight` adds
  engine reachability -> `{{"status": "ok", "hindsight": "ok"}}`.

## Auth

Unauthenticated on the internal network; access is mediated by the deploy edge.
Prefer the internal base URL `http://memory:8080`.

## Owners

Every call carries `owner_id` — the memory scope. Use `operator` for facts
about/for the human operator, `periodic:<preset>` for a periodic session's own
working memory, and a component/repo name for component-specific knowledge.
Recall only searches the given owner's bank.

## Endpoints

### POST /remember

Store a durable fact.

- `content` (string, required) — the fact/event to remember, self-contained.
- `owner_id` (string, required) — memory scope, e.g. `operator`.
- `tags` (optional list[str]) — topical tags for filtered recall.
- `context` (optional string) — where/why this was learned.
- `timestamp` (optional ISO 8601) — when the fact became true.
- `document_id` (optional string) — groups facts under one source document.
- `background` (optional bool, default false) — true runs the engine's fact
  extraction asynchronously: the call returns as soon as the item is queued
  instead of waiting out the LLM pipeline. Use for fire-and-forget writes like
  rolling summaries.
- `update_mode` (optional `append` | `replace`) — with `document_id`, `replace`
  supersedes the facts previously retained under that document (rolling-summary
  dedup), `append` adds to them. Default is the engine's append behavior.

Write full sentences with names spelled out (no pronouns or session-relative
references) — the memory outlives the conversation that wrote it.

### GET /recall

Search an owner's memories.

- `query` (string, required) — natural-language search.
- `owner_id` (string, required).
- `limit` (optional int, default 10).
- `tags` (optional repeated tag filter).
- `budget` (optional `low` | `mid` | `high`) — engine search breadth; default
  is derived from limit (`low` for limit <= {RECALL_BUDGET_LOW_MAX_LIMIT}, `mid`
  up to {RECALL_BUDGET_MID_MAX_LIMIT}). Higher budgets are slower (cross-encoder
  reranks every candidate); only raise it for a deliberately wide search.

Returns ranked memories and consolidated observations. Keep limit small for
per-turn context (8 is plenty); recall latency scales with the engine budget,
not with the number of results returned.

### POST /reflect

Reason over an owner's memories.

- `query` (string, required) — question to reason about.
- `owner_id` (string, required).

Returns a synthesized answer grounded in the owner's memories (slower than
`/recall`; use for judgment questions, not lookups).
"""
