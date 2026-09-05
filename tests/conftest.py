"""Shared respx fixtures for the robotsix-memory test suite.

The Hindsight routes are registered once here (the respx
"pre-configured router" pattern) so individual tests attach only the
``return_value``/``side_effect`` they care about and inspect ``.calls``
for request-body assertions. Route strings live in exactly one place.

Fixture shapes mirror the live Hindsight 0.9.2 REST API (retain = POST
/memories, recall = POST /memories/recall).
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import httpx
import pytest
import respx

from robotsix_memory.hindsight_client import bank_id
from robotsix_memory.main import settings

HS = settings.hindsight_url.rstrip("/")
OPERATOR_BANK = bank_id(settings.bank_prefix, "operator")

RETAIN_PATH = f"{HS}/v1/default/banks/{OPERATOR_BANK}/memories"
RECALL_PATH = f"{HS}/v1/default/banks/{OPERATOR_BANK}/memories/recall"
REFLECT_PATH = f"{HS}/v1/default/banks/{OPERATOR_BANK}/reflect"
PING_PATH = f"{HS}/"


@pytest.fixture
def hindsight_mock() -> Iterator[SimpleNamespace]:
    """Pre-register the default Hindsight routes with respx.

    Yields a namespace exposing ``routes`` (short name -> respx route)
    and ``bank`` (the operator bank id). Tests override only the
    ``return_value``/``side_effect`` and read ``.calls`` for body checks.
    """
    with respx.mock(assert_all_called=False) as router:
        routes = {
            "retain": router.post(RETAIN_PATH).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "success": True,
                        "bank_id": OPERATOR_BANK,
                        "items_count": 1,
                        "async": False,
                    },
                )
            ),
            "recall": router.post(RECALL_PATH).mock(
                return_value=httpx.Response(200, json={"results": [], "entities": {}})
            ),
            "reflect": router.post(REFLECT_PATH).mock(
                return_value=httpx.Response(200, json={"answer": ""})
            ),
            "ping": router.get(PING_PATH).mock(
                return_value=httpx.Response(200, json={"name": "hindsight"})
            ),
        }
        yield SimpleNamespace(router=router, routes=routes, bank=OPERATOR_BANK)
