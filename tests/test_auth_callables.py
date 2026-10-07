"""What the spec knows about an auth callback does not depend on its kind.

``auth=`` takes any callable, but only an ``AuthBase`` instance used directly
had its typed responses read. A function annotated ``-> User | BadToken`` was
documented without its 401, and returning ``BadToken()`` from it raised a
``ConfigError``. An ``AuthBase`` wrapped in ``functools.partial`` or behind
``functools.wraps`` lost its security scheme and its CSRF 403 as well.
"""

import functools

import pytest
from openapi_contract import export_contract, validate_response

from hattori import ApiError, HattoriAPI, Schema
from hattori.openapi.docs import _csrf_needed
from hattori.responses import resolve_api_return_schema
from hattori.security import APIKeyCookie, HttpBearer
from hattori.security.base import auth_layers
from hattori.testing import TestAsyncClient, TestClient

BAD_TOKEN = {"$ref": "#/components/schemas/BadToken"}
WRONG_ROLE = {"$ref": "#/components/schemas/WrongRole"}
HTTP_ERROR = {"$ref": "#/components/schemas/HttpErrorResponse"}


class Out(Schema):
    ok: bool


class BadToken(ApiError):
    code = 401
    error_code = "bad_token"
    message = "Token invalid"


class WrongRole(ApiError):
    code = 403
    error_code = "wrong_role"
    message = "Role not allowed"


def by_token(request) -> str | BadToken | None:
    token = request.headers.get("X-Token")
    if token is None:
        return None
    return token if token in ("admin", "member") else BadToken()


def with_role(request, role: str) -> str | BadToken | WrongRole | None:
    user = by_token(request)
    if isinstance(user, str) and user != role:
        return WrongRole()
    return user


async def by_token_async(request) -> str | BadToken | None:
    return by_token(request)


class ByToken:
    """A callable that is not an ``AuthBase``."""

    def __call__(self, request) -> str | BadToken | None:
        return by_token(request)


class Bearer(HttpBearer):
    def authenticate(self, request, token: str) -> str | BadToken:
        return token if token in ("admin", "member") else BadToken()


class Cookie(APIKeyCookie):
    def authenticate(self, request, key) -> str | BadToken | None:
        if key is None:
            return None
        return key if key in ("admin", "member") else BadToken()


def logged(auth):
    @functools.wraps(auth)
    def wrapper(request):
        return auth(request)

    return wrapper


def _api(auth, method="get") -> HattoriAPI:
    api = HattoriAPI()

    @api.api_operation([method.upper()], "/view", auth=auth)
    def view(request) -> Out:
        return Out(ok=True)

    return api


def _schema(document, status, method="get"):
    response = document["paths"]["/api/view"][method]["responses"][status]
    return response["content"]["application/json"]["schema"]


@pytest.mark.parametrize(
    "auth",
    [by_token, ByToken(), logged(by_token), functools.partial(by_token)],
    ids=["function", "callable object", "wrapped function", "partial function"],
)
def test_callable_auth_documents_and_sends_its_typed_responses(auth):
    api = _api(auth)
    document = export_contract(api)
    client = TestClient(api)

    rejected = client.get("/view", headers={"X-Token": "nope"})
    declined = client.get("/view")
    allowed = client.get("/view", headers={"X-Token": "admin"})

    assert rejected.status_code == 401
    assert rejected.json() == {"code": "bad_token", "message": "Token invalid"}
    assert declined.json() == {"detail": "Unauthorized"}
    assert allowed.status_code == 200
    validate_response(document, "/api/view", rejected)
    validate_response(document, "/api/view", declined)
    assert _schema(document, "401") == {"anyOf": [BAD_TOKEN, HTTP_ERROR]}


def test_partial_auth_is_read_from_the_function_it_binds():
    api = _api(functools.partial(with_role, role="admin"))
    document = export_contract(api)
    client = TestClient(api)

    denied = client.get("/view", headers={"X-Token": "member"})
    allowed = client.get("/view", headers={"X-Token": "admin"})

    assert denied.status_code == 403
    assert denied.json() == {"code": "wrong_role", "message": "Role not allowed"}
    assert allowed.status_code == 200
    validate_response(document, "/api/view", denied)
    assert _schema(document, "403") == WRONG_ROLE


@pytest.mark.asyncio
async def test_async_function_auth_documents_and_sends_its_typed_responses():
    api = HattoriAPI()

    @api.get("/view", auth=by_token_async)
    async def view(request) -> Out:
        return Out(ok=True)

    document = export_contract(api)
    client = TestAsyncClient(api)

    rejected = await client.get("/view", headers={"X-Token": "nope"})
    allowed = await client.get("/view", headers={"X-Token": "member"})

    assert rejected.status_code == 401
    assert rejected.json() == {"code": "bad_token", "message": "Token invalid"}
    assert allowed.status_code == 200
    validate_response(document, "/api/view", rejected)


def test_function_auth_without_typed_responses_documents_only_the_default():
    def plain(request):
        return request.headers.get("X-Token")

    document = export_contract(_api(plain))

    assert _schema(document, "401") == HTTP_ERROR


@pytest.mark.parametrize(
    "wrap",
    [lambda auth: auth, functools.partial, logged],
    ids=["direct", "partial", "wrapped"],
)
def test_wrapped_auth_class_is_documented_like_the_class_itself(wrap):
    api = _api(wrap(Bearer()))
    document = export_contract(api)

    rejected = TestClient(api).get("/view", headers={"Authorization": "Bearer nope"})

    assert rejected.json() == {"code": "bad_token", "message": "Token invalid"}
    validate_response(document, "/api/view", rejected)
    assert document["paths"]["/api/view"]["get"]["security"] == [{"Bearer": []}]
    assert document["components"]["securitySchemes"] == {
        "Bearer": {"type": "http", "scheme": "bearer"}
    }
    assert _schema(document, "401") == {"anyOf": [BAD_TOKEN, HTTP_ERROR]}


class CsrfEnforcingClient(TestClient):
    def _build_request(self, *args, **kwargs):
        request = super()._build_request(*args, **kwargs)
        request._dont_enforce_csrf_checks = False
        return request


@pytest.mark.parametrize(
    "wrap",
    [lambda auth: auth, functools.partial, logged],
    ids=["direct", "partial", "wrapped"],
)
def test_wrapped_cookie_auth_documents_its_csrf_403(wrap):
    api = _api(wrap(Cookie()), method="post")
    document = export_contract(api)

    rejected = CsrfEnforcingClient(api).post("/view", COOKIES={"key": "admin"})

    assert rejected.status_code == 403
    assert rejected.json() == {"detail": "CSRF check Failed"}
    validate_response(document, "/api/view", rejected, method="post")
    assert _schema(document, "403", method="post") == HTTP_ERROR
    assert document["paths"]["/api/view"]["post"]["security"] == [{"Cookie": []}]


@pytest.mark.parametrize(
    "wrap",
    [lambda auth: auth, functools.partial, logged],
    ids=["direct", "partial", "wrapped"],
)
def test_docs_ask_for_a_csrf_token_behind_wrapped_cookie_auth(wrap):
    assert _csrf_needed(HattoriAPI(auth=wrap(Cookie()))) is True
    assert _csrf_needed(HattoriAPI(auth=wrap(Cookie(csrf=False)))) is False
    assert _csrf_needed(HattoriAPI(auth=wrap(Bearer()))) is False


def test_what_a_wrapper_declares_comes_before_what_it_wraps():
    """A wrapper may answer for itself, and then its own declarations stand."""

    def allow_all(request) -> str:
        return "anyone"

    @functools.wraps(allow_all)
    def admins_only(request):
        user = request.headers.get("X-Token")
        return user if user == "admin" else WrongRole()

    admins_only.auth_responses = {403: resolve_api_return_schema(WrongRole)}
    scheme = {"type": "apiKey", "in": "header", "name": "X-Token"}
    admins_only.openapi_security_schema = scheme
    admins_only.csrf = True

    api = _api(admins_only)
    document = export_contract(api)

    denied = TestClient(api).get("/view", headers={"X-Token": "member"})

    assert denied.status_code == 403
    assert denied.json() == {"code": "wrong_role", "message": "Role not allowed"}
    validate_response(document, "/api/view", denied)
    assert _schema(document, "403") == WRONG_ROLE
    assert document["components"]["securitySchemes"] == {"function": scheme}
    assert _csrf_needed(HattoriAPI(auth=admins_only)) is True


def test_docs_csrf_token_follows_what_the_wrapper_declares():
    wrapper = logged(Cookie())
    wrapper.csrf = False

    assert _csrf_needed(HattoriAPI(auth=wrapper)) is False
    # The cookie auth underneath checks CSRF all the same, so the 403 stays.
    document = export_contract(_api(wrapper, method="post"))
    assert _schema(document, "403", method="post") == HTTP_ERROR


def test_wrapper_can_clear_what_it_wraps():
    wrapper = logged(Bearer())
    wrapper.openapi_security_schema = None
    wrapper.auth_responses = None

    document = export_contract(_api(wrapper))

    assert "security" not in document["paths"]["/api/view"]["get"]
    assert "securitySchemes" not in document["components"]
    assert _schema(document, "401") == HTTP_ERROR


def test_security_scheme_may_be_computed_on_each_read():
    class Computed:
        @property
        def openapi_security_schema(self):
            return {"type": "http", "scheme": "bearer"}

        def __call__(self, request):
            return request.headers.get("Authorization")

    for auth in (Computed(), functools.partial(Computed())):
        document = export_contract(_api(auth))

        assert document["paths"]["/api/view"]["get"]["security"] == [{"Computed": []}]
        assert document["components"]["securitySchemes"] == {
            "Computed": {"type": "http", "scheme": "bearer"}
        }


def test_wrapper_without_annotations_is_read_from_what_it_wraps():
    def wrapper(request):
        return by_token(request)

    wrapper.__wrapped__ = by_token

    document = export_contract(_api(wrapper))

    assert _schema(document, "401") == {"anyOf": [BAD_TOKEN, HTTP_ERROR]}


def test_layers_are_followed_to_the_end_and_odd_wrappers_are_survived():
    bearer = Bearer()
    wrapped = logged(functools.partial(bearer))
    assert list(auth_layers(functools.partial(wrapped)))[-1] is bearer

    def loop(request):
        return None

    loop.__wrapped__ = loop
    assert list(auth_layers(loop)) == [loop]

    def dangling(request):
        return "anyone"

    dangling.__wrapped__ = None
    assert list(auth_layers(dangling)) == [dangling]
    assert set(
        export_contract(_api(dangling))["paths"]["/api/view"]["get"]["responses"]
    ) == {
        "200",
        "401",
    }
