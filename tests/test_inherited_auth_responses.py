"""Auth applied via inheritance must contribute its declared responses.

An auth class can declare typed ``APIReturn`` outcomes in its ``authenticate``
annotation. Those responses (status code + body schema) must land in an
operation's OpenAPI spec *and* be dispatchable at runtime, regardless of whether
the auth was attached directly to the operation or inherited from a router / the
API.
"""

from typing import Literal

from hattori import APIReturn, HattoriAPI, Router, Schema
from hattori.security import HttpBearer
from hattori.security.permissions import BasePermission
from hattori.testing import TestClient


class AuthError(Schema):
    reason: str


class BadToken(APIReturn[AuthError]):
    code = 401


class Bearer(HttpBearer):
    def authenticate(self, request, token) -> object | BadToken:
        if token == "bad":
            return BadToken(AuthError(reason="bad token"))
        return {"user": 1}


def _responses(api, path):
    return api.get_openapi_schema()["paths"][path]["get"]["responses"]


def test_direct_auth_documents_401():
    api = HattoriAPI()

    @api.get("/direct", auth=Bearer())
    def direct(request) -> Schema:  # noqa: ARG001
        return {}

    assert 401 in _responses(api, "/api/direct")


def test_router_inherited_auth_documents_401():
    api = HattoriAPI()
    router = Router(auth=Bearer())

    @router.get("/me")
    def me(request) -> Schema:  # noqa: ARG001
        return {}

    api.add_router("/x", router)
    responses = _responses(api, "/api/x/me")
    assert 401 in responses
    ref = responses[401]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/AuthError")


def test_api_level_inherited_auth_documents_401():
    api = HattoriAPI(auth=Bearer())
    router = Router()

    @router.get("/me")
    def me(request) -> Schema:  # noqa: ARG001
        return {}

    api.add_router("/y", router)
    assert 401 in _responses(api, "/api/y/me")


def test_router_inherited_auth_dispatches_401_at_runtime():
    """A typed auth failure under inherited auth returns 401, not a 500."""
    api = HattoriAPI()
    router = Router(auth=Bearer())

    @router.get("/me")
    def me(request) -> Schema:  # noqa: ARG001
        return {}

    api.add_router("/x", router)
    client = TestClient(api)

    ok = client.get("/x/me", headers={"Authorization": "Bearer good"})
    assert ok.status_code == 200

    bad = client.get("/x/me", headers={"Authorization": "Bearer bad"})
    assert bad.status_code == 401
    assert bad.json() == {"reason": "bad token"}


class Denied(Schema):
    why: str


class NotAdmin(APIReturn[Denied]):
    code = 403


class IsAdmin(BasePermission):
    def check(self, request) -> Literal[True] | NotAdmin:
        return NotAdmin(Denied(why="nope"))


def test_router_inherited_permission_documents_and_dispatches_403():
    api = HattoriAPI()
    router = Router(permissions=[IsAdmin()])

    @router.get("/secret")
    def secret(request) -> Schema:  # noqa: ARG001
        return {}

    api.add_router("/z", router)

    responses = _responses(api, "/api/z/secret")
    assert 403 in responses
    ref = responses[403]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/Denied")

    client = TestClient(api)
    resp = client.get("/z/secret")
    assert resp.status_code == 403
    assert resp.json() == {"why": "nope"}
