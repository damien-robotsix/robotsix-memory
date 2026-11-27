"""Unit-tier fixtures — a bare ``HindsightClient`` with no FastAPI app.

The unit tier exercises request/response shaping of ``HindsightClient``
directly against the shared ``hindsight_mock`` respx routes, so it never
constructs a ``TestClient``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from robotsix_memory.hindsight_client import HindsightClient
from robotsix_memory.main import settings


@pytest.fixture
async def hs_client() -> AsyncIterator[HindsightClient]:
    """A client bound to the same base URL the shared respx routes mock.

    The pooled ``httpx.AsyncClient`` is created at construction and closed
    on teardown.
    """
    client = HindsightClient(
        settings.hindsight_url,
        timeout=settings.request_timeout,
        retain_timeout=settings.retain_timeout,
    )
    yield client
    await client.aclose()
