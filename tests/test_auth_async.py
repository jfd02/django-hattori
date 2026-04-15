import asyncio

import pytest

from hattori import HattoriAPI, Schema
from hattori.security import APIKeyQuery, HttpBearer
from hattori.testing import TestAsyncClient, TestClient


class KeyResult(Schema):
    key: str


class AuthResult(Schema):
    auth: str


@pytest.mark.asyncio
async def test_async_view_handles_async_auth_func():
    api = HattoriAPI()

    async def auth(request):
        key = request.GET.get("key")
        if key == "secret":
            return key

    @api.get("/async", auth=auth)
    async def view(request) -> KeyResult:
        await asyncio.sleep(0)
        return {"key": request.auth}

    client = TestAsyncClient(api)

    # Actual tests --------------------------------------------------

    # without auth:
    res = await client.get("/async")
    assert res.status_code == 401

    # async successful
    res = await client.get("/async?key=secret")
    assert res.json() == {"key": "secret"}


@pytest.mark.asyncio
async def test_async_view_handles_async_auth_cls():
    api = HattoriAPI()

    class Auth:
        async def __call__(self, request):
            key = request.GET.get("key")
            if key == "secret":
                return key

    @api.get("/async", auth=Auth())
    async def view(request) -> KeyResult:
        await asyncio.sleep(0)
        return {"key": request.auth}

    client = TestAsyncClient(api)

    # Actual tests --------------------------------------------------

    # without auth:
    res = await client.get("/async")
    assert res.status_code == 401

    # async successful
    res = await client.get("/async?key=secret")
    assert res.json() == {"key": "secret"}


@pytest.mark.asyncio
async def test_async_view_handles_multi_auth():
    api = HattoriAPI()

    def auth_1(request):
        return None

    async def auth_2(request):
        return None

    async def auth_3(request):
        key = request.GET.get("key")
        if key == "secret":
            return key

    @api.get("/async", auth=[auth_1, auth_2, auth_3])
    async def view(request) -> KeyResult:
        await asyncio.sleep(0)
        return {"key": request.auth}

    client = TestAsyncClient(api)

    res = await client.get("/async?key=secret")
    assert res.json() == {"key": "secret"}


@pytest.mark.asyncio
async def test_async_view_handles_auth_errors():
    api = HattoriAPI()

    async def auth(request):
        raise Exception("boom")

    @api.get("/async", auth=auth)
    async def view(request) -> KeyResult:
        await asyncio.sleep(0)
        return {"key": request.auth}

    @api.exception_handler(Exception)
    def on_custom_error(request, exc):
        return api.create_response(request, {"custom": True}, status=401)

    client = TestAsyncClient(api)

    res = await client.get("/async?key=secret")
    assert res.json() == {"custom": True}


@pytest.mark.asyncio
async def test_sync_authenticate_method():
    class KeyAuth(APIKeyQuery):
        async def authenticate(self, request, key):
            await asyncio.sleep(0)
            if key == "secret":
                return key

    api = HattoriAPI(auth=KeyAuth())

    @api.get("/async")
    async def async_view(request) -> AuthResult:
        return {"auth": request.auth}

    client = TestAsyncClient(api)

    res = await client.get("/async")  # NO key
    assert res.json() == {"detail": "Unauthorized"}

    res = await client.get("/async?key=secret")
    assert res.json() == {"auth": "secret"}


def test_async_authenticate_method_in_sync_context():
    class KeyAuth(APIKeyQuery):
        async def authenticate(self, request, key):
            await asyncio.sleep(0)
            if key == "secret":
                return key

    api = HattoriAPI(auth=KeyAuth())

    @api.get("/sync")
    def sync_view(request) -> AuthResult:
        return {"auth": request.auth}

    client = TestClient(api)

    res = client.get("/sync")  # NO key
    assert res.json() == {"detail": "Unauthorized"}

    res = client.get("/sync?key=secret")
    assert res.json() == {"auth": "secret"}


@pytest.mark.asyncio
async def test_async_with_bearer():
    class BearerAuth(HttpBearer):
        async def authenticate(self, request, key):
            await asyncio.sleep(0)
            if key == "secret":
                return key

    api = HattoriAPI(auth=BearerAuth())

    @api.get("/async")
    async def async_view(request) -> AuthResult:
        return {"auth": request.auth}

    client = TestAsyncClient(api)

    res = await client.get("/async")  # NO key
    assert res.json() == {"detail": "Unauthorized"}

    res = await client.get("/async", headers={"Authorization": "Bearer secret"})
    assert res.json() == {"auth": "secret"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "auth_value,response_type,expected_body",
    [
        (0, "int", 0),
        (False, "bool", False),
        ("", "str", ""),
    ],
)
async def test_async_auth_accepts_falsy_principals(
    auth_value, response_type, expected_body
):
    api = HattoriAPI()

    async def auth(request):
        if request.GET.get("key") == "ok":
            return auth_value
        return None

    if response_type == "int":

        @api.get("/async", auth=auth)
        async def view(request) -> int:
            return request.auth

    elif response_type == "bool":

        @api.get("/async", auth=auth)
        async def view(request) -> bool:
            return request.auth

    else:

        @api.get("/async", auth=auth)
        async def view(request) -> str:
            return request.auth

    client = TestAsyncClient(api)

    response = await client.get("/async?key=ok")
    assert response.status_code == 200
    assert response.json() == expected_body


@pytest.mark.asyncio
async def test_async_multi_auth_accepts_later_falsy_principal():
    api = HattoriAPI()

    async def auth_1(request):
        return None

    async def auth_2(request):
        if request.GET.get("key") == "ok":
            return 0
        return None

    @api.get("/async", auth=[auth_1, auth_2])
    async def view(request) -> int:
        return request.auth

    client = TestAsyncClient(api)

    response = await client.get("/async?key=ok")
    assert response.status_code == 200
    assert response.json() == 0
