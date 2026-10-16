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
`/health/hindsight` with `hindsight: unreachable`.

## API

| Route | Purpose |
| --- | --- |
| `POST /remember` | Store a fact (`content`, `owner_id`, optional `tags`/`context`/`timestamp`) |
| `GET /recall` | Search an owner's memories (`query`, `owner_id`, `limit`, `tags`, optional `budget` = `low`/`mid`/`high`; default derived from `limit` — `low` for `limit <= 10` — because the engine's rerank cost scales with the candidate budget) |
| `POST /reflect` | Reasoned answer grounded in the owner's memories |
| `GET /chat-skill` | Markdown+frontmatter skill document for chat agents |
| `GET /health` | Standard health check (`{"status": "ok"}`); `GET /health/live` liveness only; `GET /health/hindsight` adds engine reachability |

Memories are scoped per `owner_id` (one Hindsight bank per owner):
`operator`, `periodic:<preset>`, or a component name.

## Configuration

The component reads its runtime values from a single JSON config file
(see `config/config.json`); the deploy plane owns that file. A small set
of deploy-time environment variables override or influence runtime
behaviour and are set per deployment (see `deploy/docker-compose.yml`):

| Variable | Effect | Default |
| --- | --- | --- |
| `ENVIRONMENT` | When set to `production`, the logger emits JSON (for Datadog/ELK/CloudWatch) instead of the coloured console renderer. | unset (console renderer) |
| `MEMORY_HINDSIGHT_URL` | Overrides `hindsight_url`, the sibling Hindsight engine endpoint; the one kept per-deployment override of a settings value. | `http://memory-hindsight:8888` |
| `ROBOTSIX_CONFIG_FILE` | Path to the single JSON config file consumed by `robotsix_config.load_config`. | `config/config.json` |

## Development

```bash
uv sync
uv run pytest
uv run python -m robotsix_memory.gen_schema  # after changing Settings
```
