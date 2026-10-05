# The goal of this file is to test that mypy "likes" all the combinations of parametrization

from typing import Annotated, assert_type

from django.http import HttpRequest

from hattori import (
    JSONL,
    SSE,
    AuthedRequest,
    BasePermission,
    Body,
    BodyEx,
    HattoriAPI,
    P,
    Schema,
)
from hattori.security import HttpBearer
from hattori.testing import TestAsyncClient, TestClient
from hattori.testing.client import HattoriTestResponse


class Payload(Schema):
    x: int
    y: float
    s: str


api = HattoriAPI()


@api.post("/old_way")
def old_way(request: HttpRequest, data: Payload = Body()) -> None:
    data.s.capitalize()


@api.post("/annotated_way")
def annotated_way(request: HttpRequest, data: Annotated[Payload, Body()]) -> None:
    data.s.capitalize()


@api.post("/new_way")
def new_way(request: HttpRequest, data: Body[Payload]) -> None:
    data.s.capitalize()


@api.post("/new_way_ex")
def new_way_ex(request: HttpRequest, data: BodyEx[Payload, P(title="A title")]) -> None:
    data.s.find("")


# AuthedRequest[T] types request.auth as T, in views and in permissions alike.
# Plain HttpRequest has no `auth` attribute, so without it every read of
# request.auth needs a suppression at the call site.


class Account(Schema):
    username: str


class AccountAuth(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> Account | None:
        return Account(username=token) if token else None


class IsNamedAlice(BasePermission):
    def check(self, request: AuthedRequest[Account]) -> bool:
        return request.auth.username == "alice"


@api.get("/authed", auth=AccountAuth(), permissions=[IsNamedAlice()])
def authed(request: AuthedRequest[Account]) -> None:
    request.auth.username.capitalize()
    # Inherited HttpRequest members stay available.
    request.headers.get("Authorization")


# Streaming markers accept item types in annotations, while client methods
# expose concrete response types after synchronous calls or awaiting.


def accept_streams(lines: JSONL[Payload], events: SSE[Payload]) -> None:
    pass


def sync_client_responses(client: TestClient) -> None:
    assert_type(client.get("/"), HattoriTestResponse)
    assert_type(client.post("/"), HattoriTestResponse)
    assert_type(client.put("/"), HattoriTestResponse)
    assert_type(client.patch("/"), HattoriTestResponse)
    assert_type(client.delete("/"), HattoriTestResponse)
    assert_type(client.request("GET", "/"), HattoriTestResponse)


async def async_client_responses(client: TestAsyncClient) -> None:
    assert_type(await client.get("/"), HattoriTestResponse)
    assert_type(await client.post("/"), HattoriTestResponse)
    assert_type(await client.put("/"), HattoriTestResponse)
    assert_type(await client.patch("/"), HattoriTestResponse)
    assert_type(await client.delete("/"), HattoriTestResponse)
    assert_type(await client.request("GET", "/"), HattoriTestResponse)
