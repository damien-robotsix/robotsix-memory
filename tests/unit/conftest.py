"""Unit-tier fixtures — a bare ``HindsightClient`` with no FastAPI app.

The unit tier exercises request/response shaping of ``HindsightClient``
directly against the shared ``hindsight_mock`` respx routes, so it never
constructs a ``TestClient``.
"""

from __future__ import annotations

import pytest

from robotsix_memory.hindsight_client import HindsightClient
from robotsix_memory.main import settings


@pytest.fixture
def hs_client() -> HindsightClient:
    """A client bound to the same base URL the shared respx routes mock."""
    return HindsightClient(
        settings.hindsight_url,
        timeout=settings.request_timeout,
        retain_timeout=settings.retain_timeout,
    )
