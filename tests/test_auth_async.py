import asyncio
import functools

import pytest
from django.http import HttpResponseForbidden

from hattori import HattoriAPI, Schema
from hattori.errors import ConfigError
from hattori.security import APIKeyQuery, HttpBearer
from hattori.testing import TestAsyncClient, TestClient


class KeyResult(Schema):
    key: str


class AuthResult(Schema):
    auth: str


def test_async_bearer_auth_called_once_without_warnings_in_sync_context(recwarn):
    calls = []

    class BearerAuth(HttpBearer):
        def __call__(self, request):
            calls.append("call")
            return super().__call__(request)

        async def authenticate(self, request, token):
            calls.append("authenticate")
            await asyncio.sleep(0)
            return token

    api = HattoriAPI(auth=BearerAuth())

    @api.get("/sync")
    def view(request) -> str:
        return request.auth

    response = TestClient(api).get("/sync", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    assert response.json() == "secret"
    assert calls == ["call", "authenticate"]
    assert not recwarn


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
@pytest.mark.parametrize("auth_value", [None, False, 0, "", [], {}])
async def test_async_auth_rejects_falsy_results(auth_value):
    api = HattoriAPI()

    async def auth(request):
        return auth_value

    @api.get("/async", auth=auth)
    async def view(request) -> str:
        return "reached"

    response = await TestAsyncClient(api).get("/async?key=ok")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_async_auth_written_as_a_comparison_rejects_a_wrong_key():
    class KeyMatches(APIKeyQuery):
        def authenticate(self, request, key):
            return key == "secret"

    api = HattoriAPI()

    @api.get("/async", auth=KeyMatches())
    async def view(request) -> bool:
        return request.auth

    client = TestAsyncClient(api)

    assert (await client.get("/async")).status_code == 401
    assert (await client.get("/async?key=wrong")).status_code == 401
    response = await client.get("/async?key=secret")
    assert response.status_code == 200
    assert response.json() is True


@pytest.mark.asyncio
async def test_async_multi_auth_moves_past_a_falsy_result():
    api = HattoriAPI()

    async def auth_1(request):
        return False

    async def auth_2(request):
        return request.GET.get("key")

    @api.get("/async", auth=[auth_1, auth_2])
    async def view(request) -> str:
        return request.auth

    client = TestAsyncClient(api)

    assert (await client.get("/async")).status_code == 401
    response = await client.get("/async?key=ok")
    assert response.status_code == 200
    assert response.json() == "ok"


@pytest.mark.asyncio
async def test_async_auth_returning_a_response_answers_with_it():
    reached = []

    async def refusing_auth(request):
        return HttpResponseForbidden("go away")

    api = HattoriAPI()

    @api.get("/async", auth=refusing_auth)
    async def view(request) -> str:
        reached.append("view")
        return "reached"

    response = await TestAsyncClient(api).get("/async")
    assert response.status_code == 403
    assert response.content == b"go away"
    assert reached == []


def _sync_decorator(func):
    """Wrap ``func`` so that nothing about the wrapper says ``func`` is async."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


@pytest.mark.asyncio
async def test_async_auth_behind_a_sync_decorator_is_awaited_on_async_endpoint(recwarn):
    class WrappedBearerAuth(HttpBearer):
        @_sync_decorator
        async def authenticate(self, request, token):
            return token if token == "secret" else None

    @_sync_decorator
    async def wrapped_key_auth(request):
        key = request.GET.get("key")
        return key if key == "secret" else None

    api = HattoriAPI()

    @api.get("/class", auth=WrappedBearerAuth())
    async def by_class(request) -> str:
        return request.auth

    @api.get("/function", auth=wrapped_key_auth)
    async def by_function(request) -> str:
        return request.auth

    client = TestAsyncClient(api)

    denied = await client.get("/class", headers={"Authorization": "Bearer wrong"})
    assert denied.status_code == 401
    allowed = await client.get("/class", headers={"Authorization": "Bearer secret"})
    assert allowed.status_code == 200
    assert allowed.json() == "secret"

    assert (await client.get("/function?key=wrong")).status_code == 401
    allowed = await client.get("/function?key=secret")
    assert allowed.status_code == 200
    assert allowed.json() == "secret"

    assert not recwarn


async def _lookup(request):
    return request.GET.get("key")


async def _forgets_an_await(request):
    return _lookup(request)


def _generator(request):
    yield request.GET.get("key")


async def _async_generator(request):
    yield request.GET.get("key")


# Each hands back something truthy whose code never ran.
_UNRUN = [
    pytest.param(_forgets_an_await, "coroutine", id="forgotten-await"),
    pytest.param(_generator, "generator", id="generator"),
    pytest.param(_async_generator, "async_generator", id="async-generator"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("auth,kind", _UNRUN)
async def test_auth_result_that_has_not_run_is_refused_on_async_endpoint(auth, kind):
    api = HattoriAPI()
    reached = []

    @api.get("/async", auth=auth)
    async def view(request) -> str:
        reached.append("view")
        return "reached"

    with pytest.raises(ConfigError, match=f"returned {kind} where a result"):
        await TestAsyncClient(api).get("/async?key=secret")

    assert reached == []


@pytest.mark.parametrize("auth,kind", _UNRUN)
def test_auth_result_that_has_not_run_is_refused_on_sync_endpoint(auth, kind):
    api = HattoriAPI()
    reached = []

    @api.get("/sync", auth=auth)
    def view(request) -> str:
        reached.append("view")
        return "reached"

    with pytest.raises(ConfigError, match=f"returned {kind} where a result"):
        TestClient(api).get("/sync?key=secret")

    assert reached == []


def test_auth_result_that_has_not_run_does_not_open_the_docs():
    api = HattoriAPI(auth=_generator)

    with pytest.raises(ConfigError, match="returned generator where a result"):
        TestClient(api).get("/docs?key=secret")


@pytest.mark.asyncio
async def test_auth_awaitable_that_resolves_to_itself_is_refused():
    async def auth(request):
        future = asyncio.get_running_loop().create_future()
        future.set_result(future)
        return future

    api = HattoriAPI()
    reached = []

    @api.get("/async", auth=auth)
    async def view(request) -> str:
        reached.append("view")
        return "reached"

    with pytest.raises(ConfigError, match="returned Future where a result"):
        await TestAsyncClient(api).get("/async")

    assert reached == []


class _Later:
    """An awaitable that is neither a coroutine nor from an ``async def``."""

    def __init__(self, value):
        self.value = value

    def __await__(self):
        yield from asyncio.sleep(0).__await__()
        return self.value


def _auth_by_custom_awaitable(request):
    return _Later(request.GET.get("key"))


@pytest.mark.asyncio
async def test_auth_returning_a_custom_awaitable_is_awaited_on_async_endpoint():
    api = HattoriAPI()

    @api.get("/async", auth=_auth_by_custom_awaitable)
    async def view(request) -> str:
        return request.auth

    client = TestAsyncClient(api)

    assert (await client.get("/async")).status_code == 401
    response = await client.get("/async?key=secret")
    assert response.status_code == 200
    assert response.json() == "secret"


def test_auth_returning_a_custom_awaitable_is_awaited_on_sync_endpoint():
    api = HattoriAPI()

    @api.get("/sync", auth=_auth_by_custom_awaitable)
    def view(request) -> str:
        return request.auth

    client = TestClient(api)

    assert client.get("/sync").status_code == 401
    response = client.get("/sync?key=secret")
    assert response.status_code == 200
    assert response.json() == "secret"
