# Test fixtures & suite layout

This directory splits the suite by *tier* and shares its HTTP mocking
through a single respx fixture. This document explains how that is
organised, how to consume the shared fixtures, how to add new ones, and
how to migrate older tests onto the shared layout.

## Layout

```
tests/
├── conftest.py                     # shared respx routes: hindsight_mock
├── test_config.py                  # settings / config-file tests
├── test_config_schema.py
├── test_gen_schema.py
├── unit/                           # pure HindsightClient request/response shaping
│   ├── conftest.py                 # hs_client fixture (no FastAPI app)
│   └── test_hindsight_client.py
└── api/                            # FastAPI endpoint / HTTP-contract tests
    └── test_api.py
```

Two tiers exercise different layers against the **same** mocked
Hindsight backend:

- **`tests/unit/`** — drives `HindsightClient` directly and asserts the
  request bodies it emits and the response slicing it applies. No
  FastAPI `TestClient` is constructed here.
- **`tests/api/`** — drives the FastAPI app through `TestClient` and
  asserts the fleet-facing HTTP contract (status codes, response
  envelopes, error surfacing). Hindsight is mocked at the transport
  layer.

Keeping the two tiers apart means a change to request/response shaping
fails in `unit/` (close to the cause) while a change to the HTTP
contract fails in `api/`, instead of both concerns tangling in one flat
file.

## Shared fixtures

### `hindsight_mock` (`tests/conftest.py`)

Pre-registers the default Hindsight routes with respx using the
"pre-configured router" pattern — the route strings live in exactly one
place. It yields a `SimpleNamespace` with:

- `routes` — a `dict` mapping short names to respx routes:
  `"retain"`, `"recall"`, `"reflect"`, `"ping"`. Each is pre-mocked with
  a sensible success `return_value`.
- `bank` — the operator bank id the routes are registered against.
- `router` — the underlying respx router.

The router is created with `assert_all_called=False`, so a test may
touch only the routes it cares about. The fixture mirrors the live
Hindsight 0.9.2 REST API (retain = `POST …/memories`,
recall = `POST …/memories/recall`).

### `hs_client` (`tests/unit/conftest.py`)

A bare `HindsightClient` bound to the same base URL the shared respx
routes mock. Available only to the unit tier.

## Using the shared fixtures

Override only the `return_value`/`side_effect` you care about, and read
`.calls` for request-body assertions. Do **not** re-declare the routes.

**API tier — assert the HTTP contract:**

```python
def test_remember_maps_to_retain(hindsight_mock: SimpleNamespace) -> None:
    resp = client.post(
        "/remember",
        json={"content": "…", "owner_id": "operator", "tags": ["preferences"]},
    )
    assert resp.status_code == 201
    assert hindsight_mock.routes["retain"].called


def test_health_reports_hindsight_down(hindsight_mock: SimpleNamespace) -> None:
    hindsight_mock.routes["ping"].mock(side_effect=httpx.ConnectError("refused"))
    resp = client.get("/health")
    assert resp.json() == {"status": "ok", "hindsight": "unreachable"}
```

**Unit tier — assert request/response shaping:**

```python
async def test_retain_shapes_items_and_sync_flag(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    await hs_client.retain(hindsight_mock.bank, "…", tags=["preferences"])
    body = json.loads(hindsight_mock.routes["retain"].calls[0].request.content)
    assert body["async"] is False
    assert body["items"][0]["tags"] == ["preferences"]
```

To simulate an engine error, re-mock the relevant route:

```python
hindsight_mock.routes["recall"].mock(
    return_value=httpx.Response(200, json={"results": [...], "entities": {}})
)
```

## Adding a new fixture

- **A new shared Hindsight route** → add it to the `routes` dict in
  `tests/conftest.py` with a short name and a success `return_value`, and
  define its path constant alongside the existing `RETAIN_PATH` /
  `RECALL_PATH` / … block so route strings stay in one place.
- **A tier-specific helper** → put it in that tier's `conftest.py`
  (`tests/unit/conftest.py`), not the top-level one, so it does not leak
  into unrelated tiers. Follow the `hs_client` shape.
- Type fixtures with their return/yield type (`-> HindsightClient`,
  `-> Iterator[SimpleNamespace]`) and give them a one-line docstring
  describing what a consumer gets.
- Prefer extending `hindsight_mock` over standing up a fresh
  `respx.mock` in a test — the whole point is that route strings and
  default responses live in one place.

## Migration guide

When updating an existing test to the shared layout:

1. **Place it in the right tier.** Pure `HindsightClient` shaping goes
   in `tests/unit/`; anything that drives the FastAPI app via
   `TestClient` goes in `tests/api/`.
2. **Drop bespoke respx setup.** Remove any local `respx.mock(...)`
   context manager and hard-coded route URLs; accept the
   `hindsight_mock` fixture instead and override only the route you need.
3. **Reference routes by short name.** Replace inline URL strings with
   `hindsight_mock.routes["retain"]` (etc.) and use `hindsight_mock.bank`
   for the bank id.
4. **Assert bodies via `.calls`.** Read
   `hindsight_mock.routes[name].calls[0].request.content` instead of
   inspecting a locally-held route object.
5. **Use `hs_client` in the unit tier** rather than constructing a
   `HindsightClient` by hand.
