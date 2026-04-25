from unittest.mock import Mock

import pytest
from django.utils.asyncio import async_unsafe

from hattori import HattoriAPI, Schema
from hattori.errors import AuthorizationError, ConfigError
from hattori.security import (
    APIKeyCookie,
    APIKeyHeader,
    APIKeyQuery,
    HttpBasicAuth,
    HttpBearer,
    django_auth,
    django_auth_is_staff,
    django_auth_superuser,
)
from hattori.security.base import AuthBase
from hattori.testing import TestClient
from hattori.testing.client import TestAsyncClient


def callable_auth(request):
    return request.GET.get("auth")


class KeyQuery(APIKeyQuery):
    def authenticate(self, request, key):
        if key == "keyquerysecret":
            return key


class KeyHeader(APIKeyHeader):
    def authenticate(self, request, key):
        if key == "keyheadersecret":
            return key


class CustomException(Exception):
    pass


class KeyHeaderCustomException(APIKeyHeader):
    def authenticate(self, request, key):
        if key != "keyheadersecret":
            raise CustomException
        return key


class KeyCookie(APIKeyCookie):
    def authenticate(self, request, key):
        if key == "keycookiersecret":
            return key


class BasicAuth(HttpBasicAuth):
    def authenticate(self, request, username, password):
        if username == "admin" and password == "secret":
            return username


class BearerAuth(HttpBearer):
    def authenticate(self, request, token):
        if token == "bearertoken":
            return token
        if token == "nottherightone":
            raise AuthorizationError


class AsyncBearerAuth(HttpBearer):
    """
    This one is async but in fact no awaits inside authenticate
    which led to an await error
    """

    async def authenticate(self, request, token):
        if token == "bearertoken":
            return token
        if token == "nottherightone":
            raise AuthorizationError


class AuthResult(Schema):
    auth: str


def demo_operation(request) -> AuthResult:
    return {"auth": request.auth}


api = HattoriAPI()


@api.exception_handler(CustomException)
def on_custom_error(request, exc):
    return api.create_response(request, {"custom": True}, status=401)


for path, auth in [
    ("django_auth", django_auth),
    ("django_auth_superuser", django_auth_superuser),
    ("django_auth_is_staff", django_auth_is_staff),
    ("callable", callable_auth),
    ("apikeyquery", KeyQuery()),
    ("apikeyheader", KeyHeader()),
    ("apikeycookie", KeyCookie()),
    ("basic", BasicAuth()),
    ("bearer", BearerAuth()),
    ("async_bearer", AsyncBearerAuth()),
    ("customexception", KeyHeaderCustomException()),
]:
    api.get(f"/{path}", auth=auth, operation_id=path, url_name=path)(demo_operation)


client = TestClient(api)


class MockUser(str):
    is_authenticated = True
    is_superuser = False
    is_staff = False


class MockSuperUser(str):
    is_authenticated = True
    is_superuser = True
    is_staff = True


class MockStaffUser(str):
    is_authenticated = True
    is_superuser = False
    is_staff = True


BODY_UNAUTHORIZED_DEFAULT = dict(detail="Unauthorized")
BODY_FORBIDDEN_DEFAULT = dict(detail="Forbidden")


@pytest.mark.parametrize(
    "path,kwargs,expected_code,expected_body",
    [
        ("/django_auth", {}, 401, BODY_UNAUTHORIZED_DEFAULT),
        ("/django_auth", dict(user=MockUser("admin")), 200, dict(auth="admin")),
        ("/django_auth_superuser", {}, 401, BODY_UNAUTHORIZED_DEFAULT),
        (
            "/django_auth_superuser",
            dict(user=MockUser("admin")),
            401,
            BODY_UNAUTHORIZED_DEFAULT,
        ),
        (
            "/django_auth_superuser",
            dict(user=MockSuperUser("admin")),
            200,
            dict(auth="admin"),
        ),
        ("/django_auth_is_staff", {}, 401, BODY_UNAUTHORIZED_DEFAULT),
        (
            "/django_auth_is_staff",
            dict(user=MockUser("admin")),
            401,
            BODY_UNAUTHORIZED_DEFAULT,
        ),
        (
            "/django_auth_is_staff",
            dict(user=MockSuperUser("admin")),
            200,
            dict(auth="admin"),
        ),
        (
            "/django_auth_is_staff",
            dict(user=MockStaffUser("admin")),
            200,
            dict(auth="admin"),
        ),
        ("/callable", {}, 401, BODY_UNAUTHORIZED_DEFAULT),
        ("/callable?auth=demo", {}, 200, dict(auth="demo")),
        ("/apikeyquery", {}, 401, BODY_UNAUTHORIZED_DEFAULT),
        ("/apikeyquery?key=keyquerysecret", {}, 200, dict(auth="keyquerysecret")),
        ("/apikeyheader", {}, 401, BODY_UNAUTHORIZED_DEFAULT),
        (
            "/apikeyheader",
            dict(headers={"key": "keyheadersecret"}),
            200,
            dict(auth="keyheadersecret"),
        ),
        ("/apikeycookie", {}, 401, BODY_UNAUTHORIZED_DEFAULT),
        (
            "/apikeycookie",
            dict(COOKIES={"key": "keycookiersecret"}),
            200,
            dict(auth="keycookiersecret"),
        ),
        ("/basic", {}, 401, BODY_UNAUTHORIZED_DEFAULT),
        (
            "/basic",
            dict(headers={"Authorization": "Basic YWRtaW46c2VjcmV0"}),
            200,
            dict(auth="admin"),
        ),
        (
            "/basic",
            dict(headers={"Authorization": "YWRtaW46c2VjcmV0"}),
            200,
            dict(auth="admin"),
        ),
        (
            "/basic",
            dict(headers={"Authorization": "Basic invalid"}),
            401,
            BODY_UNAUTHORIZED_DEFAULT,
        ),
        (
            "/basic",
            dict(headers={"Authorization": "some invalid value"}),
            401,
            BODY_UNAUTHORIZED_DEFAULT,
        ),
        ("/bearer", {}, 401, BODY_UNAUTHORIZED_DEFAULT),
        (
            "/bearer",
            dict(headers={"Authorization": "Bearer bearertoken"}),
            200,
            dict(auth="bearertoken"),
        ),
        (
            "/bearer",
            dict(headers={"Authorization": "Invalid bearertoken"}),
            401,
            BODY_UNAUTHORIZED_DEFAULT,
        ),
        (
            "/bearer",
            dict(headers={"Authorization": "Bearer nonexistingtoken"}),
            401,
            BODY_UNAUTHORIZED_DEFAULT,
        ),
        (
            "/async_bearer",
            dict(headers={"Authorization": "Bearer nonexistingtoken"}),
            401,
            BODY_UNAUTHORIZED_DEFAULT,
        ),
        (
            "/async_bearer",
            dict(headers={}),
            401,
            BODY_UNAUTHORIZED_DEFAULT,
        ),
        (
            "/bearer",
            dict(headers={"Authorization": "Bearer nottherightone"}),
            403,
            BODY_FORBIDDEN_DEFAULT,
        ),
        ("/customexception", {}, 401, dict(custom=True)),
        (
            "/customexception",
            dict(headers={"key": "keyheadersecret"}),
            200,
            dict(auth="keyheadersecret"),
        ),
    ],
)
def test_auth(path, kwargs, expected_code, expected_body, settings):
    for debug in (False, True):
        settings.DEBUG = debug  # <-- making sure all if debug are covered
        response = client.get(path, **kwargs)
        assert response.status_code == expected_code
        assert response.json() == expected_body


def test_bearer_empty_token():
    """Bearer with trailing space only should return 401 without calling authenticate."""
    mock = Mock()

    class SpyBearerAuth(HttpBearer):
        def authenticate(self, request, token):
            mock(token)
            return token

    spy_api = HattoriAPI()
    spy_api.get("/spy", auth=SpyBearerAuth())(demo_operation)
    spy_client = TestClient(spy_api)

    # "Bearer " with no actual token should be rejected
    response = spy_client.get("/spy", headers={"Authorization": "Bearer "})
    assert response.status_code == 401
    mock.assert_not_called()


def test_async_auth_no_unawaited_coroutine_on_sync_endpoint():
    """Async auth on a sync endpoint should not leak unawaited coroutines."""
    import warnings

    class SpyAsyncBearerAuth(HttpBearer):
        async def authenticate(self, request, token):
            return token

    spy_api = HattoriAPI()
    spy_api.get("/spy", auth=SpyAsyncBearerAuth())(demo_operation)
    spy_client = TestClient(spy_api)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        response = spy_client.get("/spy", headers={"Authorization": "Bearer testtoken"})
        assert response.status_code == 200
        runtime_warnings = [x for x in w if issubclass(x.category, RuntimeWarning)]
        assert not runtime_warnings, f"Unawaited coroutine warnings: {runtime_warnings}"


@pytest.mark.parametrize(
    "auth_value,response_type,expected_body",
    [
        (0, "int", 0),
        (False, "bool", False),
        ("", "str", ""),
    ],
)
def test_sync_auth_accepts_falsy_principals(auth_value, response_type, expected_body):
    class FalsyAuth(APIKeyQuery):
        def authenticate(self, request, key):
            if key == "ok":
                return auth_value

    api = HattoriAPI()

    if response_type == "int":

        @api.get("/falsy", auth=FalsyAuth())
        def view(request) -> int:
            return request.auth

    elif response_type == "bool":

        @api.get("/falsy", auth=FalsyAuth())
        def view(request) -> bool:
            return request.auth

    else:

        @api.get("/falsy", auth=FalsyAuth())
        def view(request) -> str:
            return request.auth

    client = TestClient(api)

    response = client.get("/falsy?key=ok")
    assert response.status_code == 200
    assert response.json() == expected_body


def test_sync_multi_auth_accepts_later_falsy_principal():
    api = HattoriAPI()

    def auth_1(request):
        return None

    def auth_2(request):
        if request.GET.get("key") == "ok":
            return 0
        return None

    @api.get("/falsy", auth=[auth_1, auth_2])
    def view(request) -> int:
        return request.auth

    client = TestClient(api)

    response = client.get("/falsy?key=ok")
    assert response.status_code == 200
    assert response.json() == 0


def test_schema():
    schema = api.get_openapi_schema()
    assert schema["components"]["securitySchemes"] == {
        "BasicAuth": {"scheme": "basic", "type": "http"},
        "BearerAuth": {"scheme": "bearer", "type": "http"},
        "AsyncBearerAuth": {"scheme": "bearer", "type": "http"},
        "KeyCookie": {"in": "cookie", "name": "key", "type": "apiKey"},
        "KeyHeader": {"in": "header", "name": "key", "type": "apiKey"},
        "KeyHeaderCustomException": {"in": "header", "name": "key", "type": "apiKey"},
        "KeyQuery": {"in": "query", "name": "key", "type": "apiKey"},
        "SessionAuth": {"in": "cookie", "name": "sessionid", "type": "apiKey"},
        "SessionAuthSuperUser": {"in": "cookie", "name": "sessionid", "type": "apiKey"},
        "SessionAuthIsStaff": {"in": "cookie", "name": "sessionid", "type": "apiKey"},
    }
    # TODO: check operation security attributes


def test_security_scheme_name_collision_disambiguates():
    from hattori.security import APIKeyHeader

    class HeaderA(APIKeyHeader):
        param_name = "X-A"

        def authenticate(self, request, key):
            return key

    class HeaderB(APIKeyHeader):
        param_name = "X-B"

        def authenticate(self, request, key):
            return key

    HeaderA.__name__ = "ApiKey"
    HeaderB.__name__ = "ApiKey"

    api = HattoriAPI()

    @api.get("/a", auth=HeaderA())
    def view_a(request) -> str:
        return ""

    @api.get("/b", auth=HeaderB())
    def view_b(request) -> str:
        return ""

    schema = api.get_openapi_schema()
    schemes = schema["components"]["securitySchemes"]
    assert set(schemes) == {"ApiKey", "ApiKey_2"}
    assert {schemes["ApiKey"]["name"], schemes["ApiKey_2"]["name"]} == {"X-A", "X-B"}

    used_a = set(schema["paths"]["/api/a"]["get"]["security"][0])
    used_b = set(schema["paths"]["/api/b"]["get"]["security"][0])
    assert used_a != used_b
    assert used_a | used_b == {"ApiKey", "ApiKey_2"}


def test_invalid_setup():
    request = Mock()
    headers = {"Authorization": "Bearer test"}
    request.META = {"HTTP_" + k: v for k, v in headers.items()}
    request.headers = headers

    class MyAuth1(AuthBase):
        def __call__(self, *args, **kwargs):
            pass

    class MyAuth2(AuthBase):
        openapi_type = "my"

    with pytest.raises(ConfigError):
        MyAuth1()(request)
    with pytest.raises(TypeError):
        MyAuth2()(request)
    with pytest.raises(TypeError):
        APIKeyCookie()(request)
    with pytest.raises(TypeError):
        APIKeyHeader()(request)
    with pytest.raises(TypeError):
        APIKeyQuery()(request)
    with pytest.raises(TypeError):
        HttpBearer()(request)

    headers = {"Authorization": "Basic YWRtaW46c2VjcmV0"}
    request.META = {"HTTP_" + k: v for k, v in headers.items()}
    request.headers = headers

    with pytest.raises(TypeError):
        HttpBasicAuth()(request)


@pytest.mark.asyncio
async def test_async_auth():
    _sync_auth_called = False
    _async_auth_called = False
    _async_unsafe_func_called = False

    # This is the same decorator Django uses to mark its ORM functions as async unsafe,
    # which in turns raises a `SynchronousOnlyOperation` error if called
    # without `sync_to_async`.
    @async_unsafe("called without sync_to_async")
    def async_unsafe_function():
        nonlocal _async_unsafe_func_called
        _async_unsafe_func_called = True

    class AsyncAuth(APIKeyQuery):
        async def authenticate(self, request, key):
            nonlocal _async_auth_called
            _async_auth_called = True
            return None

    class SyncAuth(APIKeyQuery):
        def authenticate(self, request, key):
            async_unsafe_function()
            nonlocal _sync_auth_called
            _sync_auth_called = True
            return True

    class OkResult(Schema):
        ok: bool

    async def handle_request(request) -> OkResult:
        return {"ok": True}

    api = HattoriAPI()
    api.get("/foobar", auth=[AsyncAuth(), SyncAuth()])(handle_request)

    client = TestAsyncClient(api)
    response = await client.get("/foobar")
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    assert _sync_auth_called is True
    assert _async_auth_called is True
    assert _async_unsafe_func_called is True
