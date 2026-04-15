import asyncio

import pytest

from hattori import HattoriAPI, Schema
from hattori.security import APIKeyQuery
from hattori.testing import TestAsyncClient


class AsyncResult(Schema):
    is_async: bool


class SyncResult(Schema):
    sync: bool


@pytest.mark.asyncio
async def test_asyncio_operations():
    api = HattoriAPI()

    class KeyQuery(APIKeyQuery):
        def authenticate(self, request, key):
            if key == "secret":
                return key

    @api.get("/async", auth=KeyQuery())
    async def async_view(
        request, payload: int
    ) -> AsyncResult:
        await asyncio.sleep(0)
        return {"is_async": True}

    @api.post("/async")
    def sync_post_to_async_view(request) -> SyncResult:
        return {"sync": True}

    client = TestAsyncClient(api)

    # Actual tests --------------------------------------------------

    # without auth:
    res = await client.get("/async?payload=1")
    assert res.status_code == 401

    # async successful
    res = await client.get("/async?payload=1&key=secret")
    assert res.json() == {"is_async": True}

    # async innvalid input
    res = await client.get("/async?payload=str&key=secret")
    assert res.status_code == 422

    # async call to sync method for path that have async operations
    res = await client.post("/async")
    assert res.json() == {"sync": True}

    # invalid method
    res = await client.put("/async")
    assert res.status_code == 405
