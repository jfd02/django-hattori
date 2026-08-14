"""``AuthedRequest[T]`` is an annotation, not a runtime wrapper.

The view still receives Django's own request object; the class exists so that
``request.auth`` type-checks as ``T``. These tests pin the runtime half of that
contract: annotating with it must not change routing, auth, or permissions.
See ``tests/mypy_test.py`` for the static half, which CI checks with mypy.
"""

import pytest
from django.http import HttpRequest

from hattori import AuthedRequest, BasePermission, HattoriAPI, Schema
from hattori.security import HttpBearer
from hattori.testing import TestClient


class Account(Schema):
    username: str


class Whoami(Schema):
    username: str


class TokenAuth(HttpBearer):
    """The token is the username; empty token declines."""

    def authenticate(self, request: HttpRequest, token: str) -> Account | None:
        return Account(username=token) if token else None


class IsAlice(BasePermission):
    message = "alice only"

    def check(self, request: AuthedRequest[Account]) -> bool:
        return request.auth.username == "alice"


api = HattoriAPI()


@api.get("/me", auth=TokenAuth())
def me(request: AuthedRequest[Account]) -> Whoami:
    return Whoami(username=request.auth.username)


@api.get("/alice-only", auth=TokenAuth(), permissions=[IsAlice()])
def alice_only(request: AuthedRequest[Account]) -> Whoami:
    return Whoami(username=request.auth.username)


@api.get("/plain")
def plain(request: HttpRequest) -> Whoami:
    """Unannotated-auth control: the parameter is still skipped by name."""
    return Whoami(username="anonymous")


CAPTURED: list[HttpRequest] = []


@api.get("/capture", auth=TokenAuth())
def capture(request: AuthedRequest[Account]) -> Whoami:
    CAPTURED.append(request)
    return Whoami(username=request.auth.username)


@pytest.fixture
def client() -> TestClient:
    return TestClient(api)


def test_annotation_does_not_become_a_query_param(client: TestClient):
    """The request param is skipped by name, so the annotation can't leak into the spec."""
    schema = api.get_openapi_schema(path_prefix="")
    params = schema["paths"]["/me"]["get"].get("parameters", [])
    assert params == []
    assert "requestBody" not in schema["paths"]["/me"]["get"]


def test_auth_result_reaches_the_view(client: TestClient):
    resp = client.get("/me", headers={"Authorization": "Bearer alice"})
    assert resp.status_code == 200
    assert resp.json() == {"username": "alice"}


def test_view_receives_a_real_django_request(client: TestClient):
    """The annotation is a fiction: no AuthedRequest instance is ever constructed."""
    CAPTURED.clear()

    resp = client.get("/capture", headers={"Authorization": "Bearer bob"})

    assert resp.status_code == 200
    assert isinstance(CAPTURED[0], HttpRequest)
    assert not isinstance(CAPTURED[0], AuthedRequest)


def test_permission_reads_typed_auth(client: TestClient):
    assert (
        client.get("/alice-only", headers={"Authorization": "Bearer alice"}).status_code
        == 200
    )
    assert (
        client.get("/alice-only", headers={"Authorization": "Bearer bob"}).status_code
        == 403
    )


def test_unauthenticated_still_401s(client: TestClient):
    assert client.get("/me").status_code == 401


def test_plain_httprequest_annotation_unaffected(client: TestClient):
    assert client.get("/plain").json() == {"username": "anonymous"}
