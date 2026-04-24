"""End-to-end test for the auth-return-type pattern.

Auth classes declare possible outcomes via authenticate()'s return annotation.
Returning an APIReturn instance short-circuits to the HTTP response; the view
is never called. The OpenAPI spec picks up each APIReturn variant automatically.
"""

from hattori import ApiError, HattoriAPI, Schema
from hattori.responses import APIReturn
from hattori.security import HttpBearer
from hattori.testing import TestClient


class User(Schema):
    username: str


class BadToken(ApiError):
    code = 401
    error_code = "bad_token"
    message = "Token invalid"


class ExpiredToken(ApiError):
    code = 401
    error_code = "token_expired"
    message = "Token has expired"


class AccountLocked(ApiError):
    code = 403
    error_code = "account_locked"
    message = "Account is locked"


class TypedBearer(HttpBearer):
    def authenticate(
        self, request, token: str
    ) -> "User | BadToken | ExpiredToken | AccountLocked":
        if token == "bad":
            return BadToken()
        if token == "expired":
            return ExpiredToken()
        if token == "locked":
            return AccountLocked()
        if token == "ok":
            return User(username="alice")
        return None  # try-next-callback semantics; no callback → 401 AuthenticationError


api = HattoriAPI()


class Profile(Schema):
    username: str


@api.get("/me", auth=TypedBearer())
def me(request) -> Profile:
    user: User = request.auth
    return Profile(username=user.username)


client = TestClient(api)


def test_happy_path():
    r = client.get("/me", headers={"Authorization": "Bearer ok"})
    assert r.status_code == 200
    assert r.json() == {"username": "alice"}


def test_bad_token_short_circuits():
    r = client.get("/me", headers={"Authorization": "Bearer bad"})
    assert r.status_code == 401
    assert r.json() == {"code": "bad_token", "message": "Token invalid"}


def test_expired_token_short_circuits():
    r = client.get("/me", headers={"Authorization": "Bearer expired"})
    assert r.status_code == 401
    assert r.json() == {"code": "token_expired", "message": "Token has expired"}


def test_forbidden_short_circuits():
    r = client.get("/me", headers={"Authorization": "Bearer locked"})
    assert r.status_code == 403
    assert r.json() == {"code": "account_locked", "message": "Account is locked"}


def test_default_401_when_authenticate_returns_none():
    r = client.get("/me", headers={"Authorization": "Bearer something-else"})
    # Falls through all APIReturn branches, returns None; AuthenticationError raised.
    assert r.status_code == 401


def test_openapi_picks_up_auth_return_types():
    schema = api.get_openapi_schema()
    responses = schema["paths"]["/api/me"]["get"]["responses"]
    # The view declares Profile -> 200. Auth contributes 401 (union of BadToken +
    # ExpiredToken bodies) and 403 (AccountLocked).
    assert 200 in responses
    assert 401 in responses
    assert 403 in responses


def test_auth_responses_with_same_status_are_unionized():
    class ErrorA(Schema):
        a: str

    class ErrorB(Schema):
        b: str

    class BadA(APIReturn[ErrorA]):
        code = 401

    class BadB(APIReturn[ErrorB]):
        code = 401

    class SameStatusBearer(HttpBearer):
        def authenticate(self, request, token: str) -> User | BadA | BadB:
            if token == "a":
                return BadA(ErrorA(a="bad-a"))
            if token == "b":
                return BadB(ErrorB(b="bad-b"))
            return User(username="alice")

    same_status_api = HattoriAPI(urls_namespace="same-status-auth-responses")

    @same_status_api.get("/same-status", auth=SameStatusBearer())
    def same_status(request) -> Profile:
        user: User = request.auth
        return Profile(username=user.username)

    same_status_client = TestClient(same_status_api)
    response_a = same_status_client.get(
        "/same-status", headers={"Authorization": "Bearer a"}
    )
    response_b = same_status_client.get(
        "/same-status", headers={"Authorization": "Bearer b"}
    )

    assert response_a.status_code == 401
    assert response_a.json() == {"a": "bad-a"}
    assert response_b.status_code == 401
    assert response_b.json() == {"b": "bad-b"}
