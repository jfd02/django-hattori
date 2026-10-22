import functools

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from hattori import ApiError, HattoriAPI, Router
from hattori.errors import AuthenticationError, ConfigError
from hattori.operation import Guard
from hattori.security import APIKeyCookie, APIKeyHeader, HttpBasicAuth, HttpBearer
from hattori.testing import TestAsyncClient, TestClient


class BadToken(ApiError):
    code = 401
    error_code = "bad_token"
    message = "Invalid token"


class Bearer(HttpBearer):
    def authenticate(self, request, token) -> str | BadToken | None:
        if token == "typed":
            return BadToken()
        if token == "raised":
            raise AuthenticationError()
        if token == "raw":
            return HttpResponse("raw failure", status=401)
        if token == "explicit":
            return HttpResponse(
                status=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        if token == "forbidden":
            return HttpResponse(status=403)
        return "user" if token == "ok" else None


class Basic(HttpBasicAuth):
    def authenticate(self, request, username, password):
        return None


class Cookie(APIKeyCookie):
    def authenticate(self, request, key):
        return None


class Key(APIKeyHeader):
    def authenticate(self, request, key):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    ("token", "status", "challenge"),
    [
        (None, 401, "Bearer"),
        ("unknown", 401, "Bearer"),
        ("typed", 401, "Bearer"),
        ("raised", 401, "Bearer"),
        ("raw", 401, "Bearer"),
        ("explicit", 401, 'Bearer error="invalid_token"'),
        ("forbidden", 403, None),
        ("ok", 200, None),
    ],
)
async def test_auth_response_challenges(asynchronous, token, status, challenge):
    class AsyncBearer(Bearer):
        async def authenticate(self, request, token) -> str | BadToken | None:
            return super().authenticate(request, token)

    api = HattoriAPI(auth=AsyncBearer() if asynchronous else Bearer())

    if asynchronous:

        @api.get("/me")
        async def view(request) -> str:
            return request.auth

    else:

        @api.get("/me")
        def view(request) -> str:
            return request.auth

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if asynchronous:
        response = await TestAsyncClient(api).get("/me", headers=headers)
    else:
        response = TestClient(api).get("/me", headers=headers)
    assert response.status_code == status
    assert response.get("WWW-Authenticate") == challenge
    if token == "typed":
        assert response.json()["message"] == "Invalid token"
    if token == "raw":
        assert response.content == b"raw failure"


@pytest.mark.parametrize(
    ("auth", "challenge"),
    [
        (Basic(), 'Basic realm="api"'),
        ([Bearer(), Basic(), Bearer()], 'Bearer, Basic realm="api"'),
        ([Bearer(), Cookie()], "Bearer"),
        (Cookie(), None),
        (Key(), None),
        (lambda request: None, None),
    ],
)
def test_only_declared_challenges_are_advertised(auth, challenge):
    api = HattoriAPI(auth=auth)

    @api.get("/me")
    def view(request) -> str:
        return "ok"

    response = TestClient(api).get("/me")
    assert response.status_code == 401
    assert response.get("WWW-Authenticate") == challenge
    spec = api.get_openapi_schema()["paths"]["/api/me"]["get"]["responses"][401]
    if challenge:
        assert spec["headers"]["WWW-Authenticate"]["example"] == challenge
    else:
        assert "headers" not in spec


@pytest.mark.parametrize(
    "wrapper",
    [lambda f: f, functools.partial, lambda f: functools.wraps(f)(lambda r: f(r))],
)
def test_custom_challenge_and_wrapper_override(wrapper):
    def custom(request):
        return None

    custom.www_authenticate = 'Custom realm="customers"'
    wrapped = wrapper(custom)
    api = HattoriAPI(auth=wrapped)

    @api.get("/me")
    def view(request) -> str:
        return "ok"

    assert TestClient(api).get("/me").get("WWW-Authenticate") == custom.www_authenticate
    wrapped.www_authenticate = None
    assert Guard(auth=wrapped).auth_challenge is None


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_custom_exception_handler_preserved(asynchronous):
    api = HattoriAPI(auth=Bearer())

    def handler(request, exc):
        return HttpResponse("custom error", status=401, headers={"X-Error": "custom"})

    async def async_handler(request, exc):
        return handler(request, exc)

    api.add_exception_handler(
        AuthenticationError, async_handler if asynchronous else handler
    )

    if asynchronous:

        @api.get("/me")
        async def view(request) -> str:
            return "ok"

        response = await TestAsyncClient(api).get("/me")
    else:

        @api.get("/me")
        def view(request) -> str:
            return "ok"

        response = TestClient(api).get("/me")
    assert response.status_code == 401
    assert response.content == b"custom error"
    assert response.get("X-Error") == "custom"
    assert response.get("WWW-Authenticate") == "Bearer"


def test_endpoint_errors_head_and_mount_overrides():
    router = Router()

    @router.get("/me")
    def view(request) -> BadToken:
        return BadToken()

    api = HattoriAPI(auth=Bearer())
    api.add_router("/bearer", router, url_name_prefix="bearer")
    api.add_router("/cookie", router, auth=Cookie(), url_name_prefix="cookie")
    api.add_router("/public", router, auth=None, url_name_prefix="public")
    client = TestClient(api)
    response = client.request(
        "HEAD", "/bearer/me", headers={"Authorization": "Bearer ok"}
    )
    assert response.status_code == 401
    assert response.get("WWW-Authenticate") == "Bearer"
    assert client.get("/cookie/me").get("WWW-Authenticate") is None
    assert client.get("/public/me").get("WWW-Authenticate") is None


def test_documentation_guard_challenge_and_explicit_header():
    api = HattoriAPI()
    guard = Guard(api, auth=Bearer())
    response = guard.refuse(RequestFactory().get("/docs"), {})
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    request = RequestFactory().get("/docs", HTTP_AUTHORIZATION="Bearer explicit")
    response = guard.refuse(request, {})
    assert response["WWW-Authenticate"] == 'Bearer error="invalid_token"'


@pytest.mark.parametrize(
    "value", [False, 123, "", " ", "Bearer\r\nX-Foo: bar", "Bearer\x00"]
)
def test_invalid_challenge_rejected_at_declaration(value):
    auth = Bearer()
    auth.www_authenticate = value
    with pytest.raises(ConfigError, match="www_authenticate"):
        Guard(auth=auth)


def test_auth_metadata_does_not_invent_a_challenge():
    def auth(request):
        return None

    auth.openapi_type = "http"
    auth.openapi_scheme = "bearer"
    assert Guard(auth=auth).auth_challenge is None


def test_bearer_challenge_survives_terminal_session_failure():
    class TerminalSession(Cookie):
        can_decline = False

        def authenticate(self, request, key) -> str | BadToken:
            return BadToken()

    api = HattoriAPI(auth=[Bearer(), TerminalSession()])

    @api.get("/me")
    def view(request) -> str:
        return "ok"

    response = TestClient(api).get("/me")
    assert response.status_code == 401
    assert response.json()["code"] == "bad_token"
    assert response.get("WWW-Authenticate") == "Bearer"


def test_custom_realm_is_used_at_runtime_and_in_schema():
    class CustomerBearer(Bearer):
        www_authenticate = 'Bearer realm="customers"'

    api = HattoriAPI(auth=CustomerBearer())

    @api.get("/me")
    def view(request) -> str:
        return "ok"

    response = TestClient(api).get("/me")
    assert response.get("WWW-Authenticate") == 'Bearer realm="customers"'
    headers = api.get_openapi_schema()["paths"]["/api/me"]["get"]["responses"][401][
        "headers"
    ]
    assert headers["WWW-Authenticate"]["example"] == 'Bearer realm="customers"'
