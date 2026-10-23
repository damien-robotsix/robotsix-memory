# Hindsight 0.10.2 → 1.1.0: migration, schema-drift risk and rollback plan

Status: **evaluation only — not executed.** The operator approved this plan as
an evaluation (Option B, 2026-10-23). Do not do any live data migration, and do
not merge Dependabot PR #89, until the operator has reviewed this document.

Scope: the `memory-hindsight` service in `deploy/docker-compose.yml`
(`ghcr.io/vectorize-io/hindsight:0.10.2` → `1.1.0`). Its embedded pg0 Postgres
lives on the `memory-hindsight-data` named volume (`/home/hindsight/.pg0`).

Source of the findings: the upstream repository diffed at tags `v0.10.2` and
`v1.1.0`: the changelog (`hindsight-docs/src/pages/changelog/index.md`), the
Alembic migrations in `hindsight-api-slim/hindsight_api/alembic/versions/`,
the API routers and `uv.lock`. The changelog page at `v1.1.0` stops at
`1.0.0`, so the 1.0.0 → 1.1.0 delta was taken from the commit log (229
commits).

## 1. Breaking changes and how they affect us

| Upstream change | Version | Effect on robotsix-memory |
| --- | --- | --- |
| Tag-scoped API callers are rejected from whole-bank operations | 1.0.0 | **None expected.** The wrapper calls the engine without auth and without tag scopes. Recall `tags` are request filters, not caller scopes. Check this anyway in the end-to-end check (§4). |
| Worker flags `--max-retries` / `--log-level` removed | 0.10.3 | **None.** The deploy uses the image entrypoint and passes no worker flags. |
| Store returns 503 + `Retry-After` when unavailable (previously 500) | 1.0.0 | Low. `HindsightClient` already turns any non-2xx into `HindsightError`. |
| Overlong recall queries are truncated instead of returning 400 | 0.10.3 | Low and positive. `shrink_recall_query` becomes a fallback that never runs. Leave it in place. |
| Operations can now be `deferred` (e.g. embeddings out of credits); `retry` runs a deferred operation immediately | 1.1.0 | **Medium.** `retain_replay.py` only replays operations whose `status == "failed"` and treats `completed`/`success` as healthy. A `deferred` retain is neither, so it is not replayed. This matches what we want (the engine retries it itself), but `/health/hindsight` retain status must be checked with a deferred op present (§4). |

The API routes the wrapper calls are all still present at `v1.1.0`:
`GET /v1/default/banks`, `POST …/banks/{bank}/memories`,
`POST …/memories/recall`, `POST …/reflect`, `GET …/operations` and
`POST …/operations/{id}/retry`.

## 2. Persisted-store schema drift

Hindsight runs its Alembic migrations automatically at startup against the
pg0 database. Going from 0.10.2 to 1.1.0 applies these revisions:

| Revision | Kind | Reversible? | Risk |
| --- | --- | --- | --- |
| `c8e2f4a6b1d3` Oracle Text index for knowledge pages | DDL (Oracle only) | n/a | None on PG. |
| `b2429c65cc20` `banks_needing_consolidation()` per-bank probe | Replaces a function | Downgrade is a no-op (same signature) | Low. |
| `d4f7a2c9e1b5` scheduled full refresh for mental models | Adds a column and a partial index; **drops and recreates** `mental_models_with_cron()` with a new result type | Partial | Low–medium. 0.10.2 ignores the extra column, but this is a function-signature change. |
| `e8c1a4f7b2d9` drop global `memory_units` vector indexes | **Drops indexes** (`hnsw` etc.) not served by per-bank partial indexes | No | Medium. Recall uses the per-bank `idx_mu_emb_*` indexes, but if a bank has no per-bank index, recall may fall back to a seq-scan. Measure recall latency (§4). The migration fails fast on a write-busy table (lock guard), so run it with the wrapper stopped. |
| `f2a9c4e7b1d3` widen `mental_model_id` refs to `TEXT` | `ALTER COLUMN TYPE` | No (narrowing would truncate) | Low. Table rewrite on `mental_model_history` / `knowledge_pages`, which are small for us. |
| `c7e2f9a4d1b8` clear placeholder `event_date` values | **Data mutation**: `UPDATE memory_units SET event_date = NULL` for year-1/9999 dates | **No (lossy by design)** | Low–medium. It only touches placeholder dates (which already broke retain on PG ≤ 16), but it cannot be undone. A rollback needs the volume snapshot. |

The upstream release also edited two old migrations in place
(`a7b8c9d0e1f2`, `a9b8c7d6e5f4`: `VARCHAR(64)` → `TEXT`). That only changes
fresh installs. Existing databases get the same change from `f2a9c4e7b1d3`.

The embedded Postgres client moves from `pg0-embedded` 0.15.0 to 0.15.2, a
patch bump. I found no evidence of a Postgres **major** version change, which
would make the data directory unreadable. Still confirm it before the rollout:
compare `PG_VERSION` in the volume before and after (§3, step 2).

**Conclusion:** existing memory data is expected to stay forward-compatible.
The engine migrates it in place at startup, and no re-index or re-embedding is
needed because the embedding model and dimension are unchanged (bge-m3).
**The upgrade is one-way:** `c7e2f9a4d1b8`, `e8c1a4f7b2d9` and `f2a9c4e7b1d3`
cannot be downgraded. After the migration has run, the only rollback path is
restoring the volume snapshot.

## 3. Migration procedure (for operator approval — not executed)

1. **Freeze writes.** Stop the `memory` wrapper (`docker compose stop memory`)
   so no retain or recall reaches the engine. Then check that the engine has
   no in-flight operations (`GET /v1/default/banks/{bank}/operations` shows no
   `pending`/`processing` op).
2. **Snapshot the volume.** Stop `memory-hindsight`, then make a cold copy of
   `memory-hindsight-data`. For example:
   `docker run --rm -v memory-hindsight-data:/src -v "$PWD":/dst alpine tar czf /dst/hindsight-0.10.2-$(date +%F).tgz -C /src .`
   Record `PG_VERSION` and the current Alembic head
   (`SELECT version_num FROM alembic_version`).
3. **Rehearse on a copy first.** Restore the snapshot into a scratch volume.
   Start `ghcr.io/vectorize-io/hindsight:1.1.0` (digest-pinned, as in PR #89)
   against it on an isolated network, and run §4 against that instance. Only
   continue if every check passes.
4. **Roll production.** Merge PR #89 (or apply its digest-pinned tag), run
   `docker compose up -d memory-hindsight`, and follow the logs until the
   migrations reach the 1.1.0 head and the API reports healthy. Then start
   `memory`.
5. **Verify** with §4 against production, and watch `/health/hindsight` and
   the recall-latency metrics for 24 h.

## 4. End-to-end verification checklist

Run against the rehearsal instance (step 3) and again after the rollout
(step 5):

- [ ] Engine logs show every migration above applied with no error, and
  `alembic_version` is at the 1.1.0 head.
- [ ] `PG_VERSION` is the same as before the upgrade.
- [ ] `GET /health/hindsight` → `hindsight: ok`, `retain.status: ok`.
- [ ] For an existing owner: `GET /recall` with a known query returns facts
  stored **before** the upgrade, with the same top results as a baseline
  captured from 0.10.2 before step 1.
- [ ] `POST /remember`, then `GET /recall`: the new fact is retained,
  consolidated and recalled.
- [ ] `POST /reflect` returns an answer that cites memory IDs.
- [ ] Recall latency at `budget=low` is no worse than the 0.10.2 baseline,
  since `e8c1a4f7b2d9` dropped global vector indexes.
- [ ] Retain-replay: a forced failed retain is still detected as `failing` and
  replayed. A `deferred` operation does not flip `/health/hindsight` to
  `degraded` by mistake.
- [ ] `HINDSIGHT_API_RERANKER_MAX_CANDIDATES_LOW` is still honoured (recall
  latency stays in the p50 range seen after the 2026-09-08 fix).

## 5. Rollback

- **Before step 4** (only the rehearsal ran): discard the scratch volume.
  Production is untouched.
- **After step 4:** re-pinning the image to `0.10.2` is **not** a valid
  rollback on its own. The migrated schema contains irreversible changes, and
  0.10.2 would start against a database whose Alembic head it does not know.
  Instead:
  1. `docker compose stop memory memory-hindsight`.
  2. Revert the image line in `deploy/docker-compose.yml` to
     `ghcr.io/vectorize-io/hindsight:0.10.2@sha256:d1840062a5b79940ab7a9f4809ceb90fc776d4ad737cd9329e9b5836cc64ab70`.
  3. Empty the `memory-hindsight-data` volume and restore the step-2 snapshot
     into it.
  4. `docker compose up -d`, then run the §4 recall checks against 0.10.2.
- **Data written after the upgrade** (retains made under 1.1.0) is lost on
  rollback. Keep the rollout window short. If needed, re-submit
  `/remember` calls from the fleet's logs.

## 6. Recommendation

The bump looks safe to roll **if** the rehearsal on a snapshot copy (§3,
step 3) passes §4. No code change in the wrapper is required. Hold PR #89
open until the operator approves running step 3 onward.
