import asyncio
from functools import wraps

import pytest
from django.core.exceptions import PermissionDenied

from hattori import HattoriAPI, Schema
from hattori.decorators import decorate_view
from hattori.errors import HttpError
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
    async def async_view(request, payload: int) -> AsyncResult:
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
    assert res.json() == {"detail": "Method Not Allowed"}

    # HEAD falls back to the GET operation
    res = await client.request("HEAD", "/async?payload=1&key=secret")
    assert res.status_code == 200


def _on_event_loop() -> bool:
    # What Django checks before it lets synchronous code such as the ORM run.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@pytest.mark.asyncio
async def test_exception_handlers_never_run_on_the_event_loop():
    # A handler is synchronous code and may use the ORM, which Django refuses
    # to run on the event loop.
    api = HattoriAPI()
    on_loop = []

    @api.exception_handler(HttpError)
    def handler(request, exc):
        on_loop.append(_on_event_loop())
        return api.create_response(request, {}, status=exc.status_code)

    def denies(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            raise PermissionDenied

        return wrapper

    def denies_async(view):
        @wraps(view)
        async def wrapper(request, *args, **kwargs):
            raise PermissionDenied

        return wrapper

    @api.get("/mixed")
    async def read(request) -> str:
        return "async"

    @api.post("/mixed")
    @decorate_view(denies)
    def write(request) -> str:
        return "sync"

    @api.get("/decorated")
    @decorate_view(denies_async)
    async def decorated(request) -> str:
        return "async"

    client = TestAsyncClient(api)

    assert (await client.put("/mixed")).status_code == 405
    assert (await client.post("/mixed")).status_code == 403
    assert (await client.get("/decorated")).status_code == 403
    assert on_loop == [False, False, False]
