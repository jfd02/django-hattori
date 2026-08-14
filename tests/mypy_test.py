# The goal of this file is to test that mypy "likes" all the combinations of parametrization

from typing import Annotated

from django.http import HttpRequest

from hattori import AuthedRequest, BasePermission, Body, BodyEx, HattoriAPI, P, Schema
from hattori.security import HttpBearer


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
