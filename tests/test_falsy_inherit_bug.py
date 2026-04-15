"""
Test for and/or anti-pattern bug where falsy values (None, [], False) are not
properly inherited from HattoriAPI/Router to child operations.

The pattern `x is SENTINEL and self.x or x` fails when self.x is falsy because:
  True and None -> None, then None or SENTINEL -> SENTINEL

The correct pattern is: `self.x if x is SENTINEL else x`

This same bug exists in operation.py _set_auth:
  isinstance(auth, Sequence) and auth or [auth]
  When auth=[]: True and [] -> [], then [] or [[]] -> [[]]
"""


from hattori import HattoriAPI, Router, Schema
from hattori.constants import NOT_SET
from hattori.operation import Operation
from hattori.security import APIKeyQuery
from hattori.testing import TestClient


class OkResult(Schema):
    ok: bool


class MResult(Schema):
    m: str


class AuthResult(Schema):
    auth: str


class KeyAuth(APIKeyQuery):
    def authenticate(self, request, key):
        if key == "valid":
            return key


# -- _set_auth and/or bug (operation.py line 286) --


def test_set_auth_empty_list():
    """_set_auth([]) should result in empty auth_callbacks, not [[]]."""
    op = object.__new__(Operation)
    op.auth_callbacks = []
    op._set_auth([])
    assert (
        op.auth_callbacks == []
    ), f"Expected empty auth_callbacks but got {op.auth_callbacks!r}"


def test_set_auth_single_callable():
    """_set_auth(callable) should wrap in a list."""
    op = object.__new__(Operation)
    op.auth_callbacks = []
    auth = KeyAuth()
    op._set_auth(auth)
    assert op.auth_callbacks == [auth]


def test_set_auth_list_of_callables():
    """_set_auth([callable]) should use the list directly."""
    op = object.__new__(Operation)
    op.auth_callbacks = []
    auth = KeyAuth()
    op._set_auth([auth])
    assert op.auth_callbacks == [auth]


def test_set_auth_none_no_change():
    """_set_auth(None) should not modify auth_callbacks."""
    op = object.__new__(Operation)
    op.auth_callbacks = []
    op._set_auth(None)
    assert op.auth_callbacks == []


def test_set_auth_not_set_no_change():
    """_set_auth(NOT_SET) should not modify auth_callbacks."""
    op = object.__new__(Operation)
    op.auth_callbacks = []
    op._set_auth(NOT_SET)
    assert op.auth_callbacks == []


# -- HattoriAPI auth=None / auth=[] propagation (main.py) --


def test_api_auth_none_propagates():
    """HattoriAPI(auth=None) should disable auth on all endpoints."""
    api = HattoriAPI(auth=None)

    @api.get("/test")
    def endpoint(request) -> OkResult:
        return {"ok": True}

    client = TestClient(api)
    response = client.get("/test")
    assert response.status_code == 200


def test_api_auth_empty_list_propagates():
    """HattoriAPI(auth=[]) should disable auth on endpoints (no crash)."""
    api = HattoriAPI(auth=[])

    @api.get("/test")
    def endpoint(request) -> OkResult:
        return {"ok": True}

    client = TestClient(api)
    response = client.get("/test")
    assert response.status_code == 200


def test_api_auth_none_all_methods():
    """HattoriAPI(auth=None) should propagate through all HTTP method decorators."""
    api = HattoriAPI(auth=None)

    @api.get("/get")
    def get_ep(request) -> MResult:
        return {"m": "get"}

    @api.post("/post")
    def post_ep(request) -> MResult:
        return {"m": "post"}

    @api.put("/put")
    def put_ep(request) -> MResult:
        return {"m": "put"}

    @api.patch("/patch")
    def patch_ep(request) -> MResult:
        return {"m": "patch"}

    @api.delete("/delete")
    def delete_ep(request) -> MResult:
        return {"m": "delete"}

    @api.api_operation(["GET"], "/api-op")
    def api_op_ep(request) -> MResult:
        return {"m": "api_op"}

    client = TestClient(api)
    assert client.get("/get").status_code == 200
    assert client.post("/post").status_code == 200
    assert client.put("/put").status_code == 200
    assert client.patch("/patch").status_code == 200
    assert client.delete("/delete").status_code == 200
    assert client.get("/api-op").status_code == 200


# -- Endpoint override still works --


def test_endpoint_auth_overrides_api_none():
    """Explicit auth on endpoint should override HattoriAPI(auth=None)."""
    api = HattoriAPI(auth=None)

    @api.get("/protected", auth=KeyAuth())
    def endpoint(request) -> AuthResult:
        return {"auth": request.auth}

    client = TestClient(api)
    assert client.get("/protected").status_code == 401
    assert client.get("/protected?key=valid").status_code == 200


# -- Router by_alias=False propagation (router.py) --


def test_router_by_alias_false_propagates():
    """Router(by_alias=False): operation should receive False, not None."""
    router = Router(by_alias=False)

    @router.get("/test")
    def endpoint(request) -> OkResult:
        return {"ok": True}

    path_view = list(router.path_operations.values())[0]
    operation = path_view.operations[0]
    # by_alias=None gets coerced to False in operation.py via `or False`,
    # so the value is the same. But the router should be passing False.
    assert operation.by_alias is False


def test_router_exclude_none_false_propagates():
    """Router(exclude_none=False): operation should receive False, not None."""
    router = Router(exclude_none=False)

    @router.get("/test")
    def endpoint(request) -> OkResult:
        return {"ok": True}

    path_view = list(router.path_operations.values())[0]
    operation = path_view.operations[0]
    assert operation.exclude_none is False
