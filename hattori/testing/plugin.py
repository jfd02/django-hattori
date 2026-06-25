"""Pytest plugin shipping hattori test-client fixtures.

Opt in by adding the plugin to your top-level ``conftest.py``::

    pytest_plugins = ["hattori.testing.plugin"]

(It is intentionally *not* auto-registered via a ``pytest11`` entry point:
importing hattori touches Django settings, which aren't configured at the point
pytest loads entry-point plugins.)

The fixtures are *factories* — call them with the API or router under test::

    def test_signup(hattori_client):
        client = hattori_client(api)
        resp = client.post("/signup", json={"username": "neo", "password": "trinity!"})
        assert resp.status_code == 201

Extra keyword arguments are forwarded to the underlying client, so default
headers and cookies work the same way::

    client = hattori_client(api, headers={"Authorization": "Bearer t0ken"})

Imports of hattori are still deferred to fixture-call time so the plugin module
itself stays import-safe even before ``django.setup()`` has run.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from hattori.testing.client import TestAsyncClient, TestClient


@pytest.fixture
def hattori_client() -> Callable[..., TestClient]:
    """Factory for a synchronous :class:`~hattori.testing.TestClient`."""
    from hattori.testing.client import TestClient

    def _make(router_or_app: Any, **kwargs: Any) -> TestClient:
        return TestClient(router_or_app, **kwargs)

    return _make


@pytest.fixture
def hattori_async_client() -> Callable[..., TestAsyncClient]:
    """Factory for an async :class:`~hattori.testing.TestAsyncClient`."""
    from hattori.testing.client import TestAsyncClient

    def _make(router_or_app: Any, **kwargs: Any) -> TestAsyncClient:
        return TestAsyncClient(router_or_app, **kwargs)

    return _make
