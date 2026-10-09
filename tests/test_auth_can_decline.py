"""An auth that answers every request itself says so with ``can_decline = False``.

The framework's own 401 is what a request gets when every auth on an operation
declines. For an auth that never does, the spec used to list it all the same.
It is now left out, and the auth is held to its word: a falsy result from it is
a ``ConfigError`` rather than a declined request.
"""

import functools

import pytest
from django.http import HttpResponse

from hattori import ApiError, HattoriAPI, Router, Schema
from hattori.errors import ConfigError
from hattori.security import APIKeyHeader, HttpBearer
from hattori.testing import TestAsyncClient, TestClient

HTTP_ERROR = {"$ref": "#/components/schemas/HttpErrorResponse"}
NO_KEY = {"$ref": "#/components/schemas/NoKey"}
DOCS_URLS = ["/docs", "/openapi.json"]


class Out(Schema):
    ok: bool


class NoKey(ApiError):
    code = 401
    error_code = "no_key"
    message = "Send a key"


class Locked(ApiError):
    code = 403
    error_code = "locked"
    message = "Locked"


class Key(APIKeyHeader):
    """Answers a request that has no key itself."""

    param_name = "X-Key"
    can_decline = False

    def authenticate(self, request, key) -> str | NoKey:
        return key or NoKey()


class OtherKey(APIKeyHeader):
    """Declines a request that has no key, as any auth may."""

    param_name = "X-Other"

    def authenticate(self, request, key) -> str | None:
        return key


class Broken(APIKeyHeader):
    """Says it never declines, and does."""

    param_name = "X-Key"
    can_decline = False

    def __init__(self, result=None):
        super().__init__()
        self.result = result

    def authenticate(self, request, key):
        return self.result


def logged(auth):
    @functools.wraps(auth)
    def wrapper(request):
        return auth(request)

    return wrapper


def _api(**kwargs) -> HattoriAPI:
    api = HattoriAPI(docs_auth=None)

    @api.get("/view", **kwargs)
    def view(request) -> Out:
        return Out(ok=True)

    return api


def _responses(api):
    return api.get_openapi_schema()["paths"]["/api/view"]["get"]["responses"]


def _schema(api, status):
    return _responses(api)[status]["content"]["application/json"]["schema"]


def test_auth_that_never_declines_documents_only_its_own_401():
    api = _api(auth=Key())
    client = TestClient(api)

    refused = client.get("/view")

    assert refused.status_code == 401
    assert refused.json() == {"code": "no_key", "message": "Send a key"}
    assert client.get("/view", headers={"X-Key": "k"}).json() == {"ok": True}
    assert _schema(api, 401) == NO_KEY


def test_no_401_is_documented_when_nothing_sends_one():
    class LockedOut(APIKeyHeader):
        param_name = "X-Key"
        can_decline = False

        def authenticate(self, request, key) -> str | Locked:
            return key or Locked()

    api = _api(auth=LockedOut())

    assert TestClient(api).get("/view").status_code == 403
    assert 401 not in _responses(api)
    assert 403 in _responses(api)


def test_auth_that_may_decline_documents_the_default_401_as_before():
    class TypedKey(APIKeyHeader):
        param_name = "X-Key"

        def authenticate(self, request, key) -> str | NoKey | None:
            return key

    assert _schema(_api(auth=OtherKey()), 401) == HTTP_ERROR
    assert _schema(_api(auth=TypedKey()), 401) == {"anyOf": [NO_KEY, HTTP_ERROR]}


def test_auth_inherited_from_the_api_is_read_the_same():
    api = HattoriAPI(auth=Key(), docs_auth=None)

    @api.get("/view")
    def view(request) -> Out:
        return Out(ok=True)

    assert _schema(api, 401) == NO_KEY


def test_router_mounted_twice_is_held_to_the_auth_of_each_mount():
    router = Router()

    @router.get("/view")
    def view(request) -> Out:
        return Out(ok=True)

    api = HattoriAPI(docs_auth=None)
    api.add_router("/kept", router, auth=Key(), url_name_prefix="kept")
    api.add_router("/broken", router, auth=Broken(), url_name_prefix="broken")
    api.add_router("/open", router, auth=OtherKey(), url_name_prefix="open")
    client = TestClient(api)
    paths = api.get_openapi_schema()["paths"]

    def schema(mount):
        response = paths[f"/api/{mount}/view"]["get"]["responses"][401]
        return response["content"]["application/json"]["schema"]

    # The auth reaches each mount only when the urls are built.
    assert client.get("/kept/view").json()["code"] == "no_key"
    assert client.get("/open/view").json() == {"detail": "Unauthorized"}
    with pytest.raises(ConfigError, match="has can_decline=False"):
        client.get("/broken/view")
    assert schema("kept") == NO_KEY
    assert schema("open") == HTTP_ERROR


def test_auth_set_on_an_operation_afterwards_is_held_to_it():
    api = _api(auth=OtherKey())
    client = TestClient(api)
    assert client.get("/view").status_code == 401

    [bound] = api._get_bound_routers()
    [operation] = bound.path_operations["/view"].operations
    operation._set_auth([Broken()])

    with pytest.raises(ConfigError, match="has can_decline=False"):
        client.get("/view")


def test_response_is_a_response_whatever_its_truth():
    class Quiet(HttpResponse):
        def __bool__(self):
            return False

    class Answers(Broken):
        def authenticate(self, request, key):
            return Quiet(status=418)

    assert TestClient(_api(auth=Answers())).get("/view").status_code == 418


def test_handler_may_answer_the_broken_promise_and_the_view_is_not_run():
    ran = []
    api = HattoriAPI(docs_auth=None)

    @api.get("/view", auth=Broken())
    def view(request) -> Out:
        ran.append(True)
        return Out(ok=True)

    @api.exception_handler(ConfigError)
    def misconfigured(request, exc):
        return api.create_response(request, {"detail": "misconfigured"}, status=500)

    response = TestClient(api).get("/view")

    assert response.status_code == 500
    assert response.json() == {"detail": "misconfigured"}
    assert not ran


@pytest.mark.parametrize(
    ("auths", "with_the_other_key"),
    [([OtherKey(), Key()], 200), ([Key(), OtherKey()], 401)],
    ids=["after-one-that-declines", "before-one-that-declines"],
)
def test_one_auth_that_never_declines_rules_the_default_401_out(
    auths, with_the_other_key
):
    api = _api(auth=auths)
    client = TestClient(api)

    # A request gets no further than the auth that never declines, so the one
    # after it is never asked.
    assert client.get("/view").json() == {"code": "no_key", "message": "Send a key"}
    other = client.get("/view", headers={"X-Other": "k"})
    assert other.status_code == with_the_other_key
    assert _schema(api, 401) == NO_KEY


@pytest.mark.parametrize("wrap", [lambda auth: auth, functools.partial, logged])
def test_it_is_read_through_what_wraps_the_auth(wrap):
    assert _schema(_api(auth=wrap(Key())), 401) == NO_KEY
    with pytest.raises(ConfigError, match="has can_decline=False"):
        TestClient(_api(auth=wrap(Broken()))).get("/view")


def test_wrapper_that_declines_for_itself_says_so_over_what_it_wraps():
    key = Key()

    @functools.wraps(key)
    def unless_blocked(request):
        return None if "X-Blocked" in request.headers else key(request)

    unless_blocked.can_decline = True
    api = _api(auth=unless_blocked)

    declined = TestClient(api).get("/view", headers={"X-Blocked": "1"})

    assert declined.status_code == 401
    assert declined.json() == {"detail": "Unauthorized"}
    assert _schema(api, 401) == {"anyOf": [NO_KEY, HTTP_ERROR]}


def test_plain_function_says_it_with_an_attribute():
    def by_header(request) -> str | NoKey:
        return request.headers.get("X-Key") or NoKey()

    by_header.can_decline = False
    api = _api(auth=by_header)

    assert TestClient(api).get("/view").json()["code"] == "no_key"
    assert _schema(api, 401) == NO_KEY


@pytest.mark.parametrize(
    ("result", "named"),
    [(None, "NoneType"), (False, "bool"), (0, "int"), ("", "str"), ([], "list")],
)
def test_auth_that_never_declines_is_held_to_it(result, named):
    client = TestClient(_api(auth=Broken(result)))

    with pytest.raises(ConfigError, match=f"returned a falsy {named}"):
        client.get("/view")


def test_what_it_returned_is_not_shown():
    class Token(dict):
        """Falsy when empty, and never to be printed."""

        def __repr__(self):
            return "secret-token"

    with pytest.raises(ConfigError) as refused:
        TestClient(_api(auth=Broken(Token()))).get("/view")

    assert "secret-token" not in str(refused.value)
    assert "Auth Broken has can_decline=False" in str(refused.value)


def test_broken_promise_is_not_made_good_by_the_auth_after_it():
    client = TestClient(_api(auth=[Broken(), OtherKey()]))

    with pytest.raises(ConfigError, match="has can_decline=False"):
        client.get("/view", headers={"X-Other": "k"})


@pytest.mark.asyncio
async def test_async_auth_is_held_to_it_too():
    async def by_header(request) -> str | NoKey:
        return request.headers.get("X-Key") or NoKey()

    async def broken(request):
        return None

    by_header.can_decline = False
    broken.can_decline = False

    def api_behind(auth):
        api = HattoriAPI(docs_auth=None)

        @api.get("/view", auth=auth)
        async def view(request) -> Out:
            return Out(ok=True)

        return api

    kept = api_behind(by_header)
    assert (await TestAsyncClient(kept).get("/view")).json()["code"] == "no_key"
    assert _schema(kept, 401) == NO_KEY
    with pytest.raises(ConfigError, match="has can_decline=False"):
        await TestAsyncClient(api_behind(broken)).get("/view")


def test_header_auth_declines_by_itself_so_cannot_say_it_never_does():
    class Bearer(HttpBearer):
        can_decline = False

        def authenticate(self, request, token) -> str | NoKey:
            return token

    client = TestClient(_api(auth=Bearer()))

    assert client.get("/view", headers={"Authorization": "Bearer t"}).json() == {
        "ok": True
    }
    # HttpBearer declines a request with no header before authenticate() runs.
    with pytest.raises(ConfigError, match="Auth Bearer has can_decline=False"):
        client.get("/view")


@pytest.mark.parametrize("url", DOCS_URLS)
def test_docs_hold_their_auth_to_it(url):
    assert TestClient(HattoriAPI(docs_auth=Key())).get(url).json()["code"] == "no_key"
    with pytest.raises(ConfigError, match="has can_decline=False"):
        TestClient(HattoriAPI(docs_auth=Broken())).get(url)


@pytest.mark.parametrize("not_a_bool", [None, "no", 0])
def test_can_decline_has_to_be_true_or_false(not_a_bool):
    class Unclear(APIKeyHeader):
        param_name = "X-Key"
        can_decline = not_a_bool

        def authenticate(self, request, key):
            return key

    with pytest.raises(ConfigError, match="can_decline must be True or False"):
        _api(auth=Unclear())
