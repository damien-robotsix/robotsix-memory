"""Chat-agent skill document for the robotsix-memory component.

Served at ``GET /chat-skill`` and read by chat agents via the deploy
roster. It teaches the fleet-facing contract only — Hindsight is an
implementation detail and its native API is deliberately not advertised.
"""

from __future__ import annotations

from typing import Any


def chat_skill() -> dict[str, Any]:
    """Return the chat-agent skill document for robotsix-memory."""
    return {
        "component": "robotsix-memory",
        "endpoint": "/chat-skill",
        "description": (
            "Long-term fleet memory. Store durable facts, preferences, and "
            "outcomes with /remember; retrieve them with /recall; ask the "
            "memory bank to reason over what it knows with /reflect. Memories "
            "are consolidated in the background into deduplicated, "
            "evidence-grounded observations per owner, so remember freely — "
            "repeats merge instead of piling up."
        ),
        "base": {
            "port": 8080,
            "health": 'GET /health -> {"status": "ok", "hindsight": "ok"}',
        },
        "auth": {
            "description": (
                "Unauthenticated on the internal network; access is mediated by the deploy edge."
            ),
            "internal": {
                "preferred": True,
                "base_url": "http://memory:8080",
            },
        },
        "owners": {
            "description": (
                "Every call carries owner_id — the memory scope. Use "
                "'operator' for facts about/for the human operator, "
                "'periodic:<preset>' for a periodic session's own working "
                "memory, and a component/repo name for component-specific "
                "knowledge. Recall only searches the given owner's bank."
            ),
        },
        "endpoints": [
            {
                "method": "POST",
                "path": "/remember",
                "body": {
                    "content": "string (required) — the fact/event to remember, self-contained",
                    "owner_id": "string (required) — memory scope, e.g. 'operator'",
                    "tags": "optional list[str] — topical tags for filtered recall",
                    "context": "optional string — where/why this was learned",
                    "timestamp": "optional ISO 8601 — when the fact became true",
                },
                "notes": (
                    "Write full sentences with names spelled out (no pronouns "
                    "or session-relative references) — the memory outlives the "
                    "conversation that wrote it."
                ),
            },
            {
                "method": "GET",
                "path": "/recall",
                "params": {
                    "query": "string (required) — natural-language search",
                    "owner_id": "string (required)",
                    "limit": "optional int (default 10)",
                    "tags": "optional repeated tag filter",
                },
                "returns": "ranked memories and consolidated observations",
            },
            {
                "method": "POST",
                "path": "/reflect",
                "body": {
                    "query": "string (required) — question to reason about",
                    "owner_id": "string (required)",
                },
                "returns": (
                    "a synthesized answer grounded in the owner's memories "
                    "(slower than /recall; use for judgment questions, not "
                    "lookups)"
                ),
            },
        ],
        "safety": {"confirmation_gated": []},
    }
