# robotsix-memory

Fleet memory component: a stable memory API (`/remember`, `/recall`,
`/reflect`) for chat agents and fleet services, backed by a
[Hindsight](https://hindsight.vectorize.io/) memory engine running as a
sibling container.

## Architecture

- **`memory`** (this repo's image): thin FastAPI wrapper owning the
  fleet-facing contract and the `/chat-skill` document. No storage of its
  own — the engine stays a swappable implementation detail.
- **`memory-hindsight`** (pinned upstream image): the Hindsight engine with
  its embedded Postgres (pg0) on a named volume. Background consolidation
  turns retained facts into deduplicated, evidence-grounded observations;
  memory is organized into typed networks (world facts, experience,
  opinions, entity observations) with two-axis temporal grounding.
- LLM (fact extraction/consolidation) via an OpenAI-compatible endpoint
  (OpenRouter); embeddings via the fleet's bge-m3 endpoint.

Crash isolation is deliberate: a native engine crash kills the sibling
container only — Docker restarts it while the wrapper keeps answering
`/health` with `hindsight: unreachable`.

## API

| Route | Purpose |
| --- | --- |
| `POST /remember` | Store a fact (`content`, `owner_id`, optional `tags`/`context`/`timestamp`) |
| `GET /recall` | Search an owner's memories (`query`, `owner_id`, `limit`, `tags`) |
| `POST /reflect` | Reasoned answer grounded in the owner's memories |
| `GET /chat-skill` | Skill document for chat agents |
| `GET /health` | Wrapper + engine status; `GET /health/live` liveness only |

Memories are scoped per `owner_id` (one Hindsight bank per owner):
`operator`, `periodic:<preset>`, or a component name.

## Development

```bash
uv sync
uv run pytest
uv run python -m robotsix_memory.gen_schema  # after changing Settings
```
