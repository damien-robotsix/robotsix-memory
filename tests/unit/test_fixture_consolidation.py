"""Regression + edge-case coverage for the consolidated respx fixtures.

The shared ``hindsight_mock`` fixture (``tests/conftest.py``) pre-registers
the Hindsight routes once and hands each test a namespace of routes to
override and inspect. Consolidating those routes into a single fixture
introduces a few failure modes that are easy to reintroduce silently:

* mock state (recorded ``.calls`` and per-test ``.mock()`` overrides)
  leaking between tests,
* the fixture behaving differently depending on the order it is requested
  relative to ``hs_client``,
* the pre-registered routes matching the wrong HTTP method/path, and
* the shared setup/teardown not re-seeding default responses each test.

These tests pin all four so a future refactor of the fixtures fails loudly.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from respx.router import AllMockedAssertionError

from robotsix_memory.hindsight_client import HindsightClient

ROUTE_NAMES = ("retain", "recall", "reflect", "ping")


# --------------------------------------------------------------------------- #
# Setup/teardown: default responses are re-seeded for every test.
# --------------------------------------------------------------------------- #
async def test_default_routes_return_seed_values(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    """Without any per-test override, every route serves its seed response."""
    retained = await hs_client.retain(hindsight_mock.bank, "seed")
    recalled = await hs_client.recall(hindsight_mock.bank, "q", limit=5)
    reflected = await hs_client.reflect(hindsight_mock.bank, "q")
    reachable = await hs_client.ping()

    assert retained["success"] is True
    assert retained["bank_id"] == hindsight_mock.bank
    assert recalled == {"results": [], "entities": {}}
    assert reflected == {"answer": ""}
    assert reachable is True


def test_fixture_exposes_all_named_routes(hindsight_mock: SimpleNamespace) -> None:
    """The consolidated namespace exposes exactly the documented routes."""
    assert set(hindsight_mock.routes) == set(ROUTE_NAMES)


def test_unused_routes_do_not_fail_teardown(hindsight_mock: SimpleNamespace) -> None:
    """assert_all_called=False lets a test touch no route without erroring."""
    for name in ROUTE_NAMES:
        assert hindsight_mock.routes[name].called is False


# --------------------------------------------------------------------------- #
# Isolation: recorded calls start empty every test; overrides do not persist.
# --------------------------------------------------------------------------- #
async def test_override_recall_is_isolated_within_test(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    """A per-test recall override applies here and records exactly one call."""
    engine = {"results": [{"text": f"f{i}"} for i in range(20)], "entities": {}}
    hindsight_mock.routes["recall"].mock(return_value=httpx.Response(200, json=engine))

    result = await hs_client.recall(hindsight_mock.bank, "q", limit=5)

    assert len(result["results"]) == 5
    assert len(hindsight_mock.routes["recall"].calls) == 1


async def test_recall_override_does_not_leak_to_next_test(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    """The override from the previous test is gone; calls start at zero."""
    for name in ROUTE_NAMES:
        assert len(hindsight_mock.routes[name].calls) == 0

    result = await hs_client.recall(hindsight_mock.bank, "q", limit=5)

    # Back to the seed response, not the 20-result override.
    assert result == {"results": [], "entities": {}}
    assert len(hindsight_mock.routes["recall"].calls) == 1


# --------------------------------------------------------------------------- #
# Fixture dependency ordering: the two fixtures compose either way round.
# --------------------------------------------------------------------------- #
async def test_ordering_client_before_mock(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    await hs_client.retain(hindsight_mock.bank, "ordered")
    assert hindsight_mock.routes["retain"].called


async def test_ordering_mock_before_client(
    hindsight_mock: SimpleNamespace, hs_client: HindsightClient
) -> None:
    await hs_client.retain(hindsight_mock.bank, "ordered")
    assert hindsight_mock.routes["retain"].called


# --------------------------------------------------------------------------- #
# Routing correctness: each client method lands on exactly one route, with
# the right HTTP method, and nothing bleeds onto a sibling route.
# --------------------------------------------------------------------------- #
async def test_retain_routes_only_to_retain(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    await hs_client.retain(hindsight_mock.bank, "content")
    called = {n for n in ROUTE_NAMES if hindsight_mock.routes[n].called}
    assert called == {"retain"}
    assert hindsight_mock.routes["retain"].calls[0].request.method == "POST"


async def test_recall_routes_only_to_recall(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    await hs_client.recall(hindsight_mock.bank, "q", limit=5)
    called = {n for n in ROUTE_NAMES if hindsight_mock.routes[n].called}
    assert called == {"recall"}
    assert hindsight_mock.routes["recall"].calls[0].request.method == "POST"


async def test_reflect_routes_only_to_reflect(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    await hs_client.reflect(hindsight_mock.bank, "q")
    called = {n for n in ROUTE_NAMES if hindsight_mock.routes[n].called}
    assert called == {"reflect"}


async def test_ping_routes_to_get_root(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    await hs_client.ping()
    called = {n for n in ROUTE_NAMES if hindsight_mock.routes[n].called}
    assert called == {"ping"}
    assert hindsight_mock.routes["ping"].calls[0].request.method == "GET"


async def test_retain_and_recall_bodies_are_recorded_separately(
    hs_client: HindsightClient, hindsight_mock: SimpleNamespace
) -> None:
    """Bodies land on their own route's ``.calls`` — no cross-contamination."""
    await hs_client.retain(hindsight_mock.bank, "a fact", tags=["t"])
    await hs_client.recall(hindsight_mock.bank, "a query", limit=3)

    retain_body = json.loads(hindsight_mock.routes["retain"].calls[0].request.content)
    recall_body = json.loads(hindsight_mock.routes["recall"].calls[0].request.content)
    assert retain_body["items"][0]["content"] == "a fact"
    assert "query" not in retain_body
    assert recall_body["query"] == "a query"
    assert "items" not in recall_body


async def test_request_to_unregistered_bank_is_not_matched(
    hindsight_mock: SimpleNamespace,
) -> None:
    """Routes are pinned to the operator bank; other banks are unmocked.

    A request to a different bank must not silently match the pre-registered
    route (which would mask a wrong-URL regression); respx raises instead.
    """
    other = HindsightClient("http://hindsight.test", timeout=5.0, retain_timeout=5.0)
    with pytest.raises(AllMockedAssertionError):
        await other._request("POST", "/v1/default/banks/other-bank/memories", json_body={})
    assert hindsight_mock.routes["retain"].called is False
