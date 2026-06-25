"""The shipped pytest plugin exposes test-client factory fixtures."""

import pytest

from hattori import Router
from hattori.testing import TestAsyncClient, TestClient

router = Router()


@router.get("/ping")
def ping(request) -> str:
    return "pong"


@router.get("/whoami")
def whoami(request) -> dict[str, str]:
    return {"agent": request.headers.get("X-Agent", "anon")}


def test_hattori_client_fixture(hattori_client):
    client = hattori_client(router)
    assert isinstance(client, TestClient)
    assert client.get("/ping").json() == "pong"


def test_hattori_client_fixture_forwards_kwargs(hattori_client):
    client = hattori_client(router, headers={"X-Agent": "ninja"})
    assert client.get("/whoami").json() == {"agent": "ninja"}


@pytest.mark.asyncio
async def test_hattori_async_client_fixture(hattori_async_client):
    client = hattori_async_client(router)
    assert isinstance(client, TestAsyncClient)
