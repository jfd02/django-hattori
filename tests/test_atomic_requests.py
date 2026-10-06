"""ATOMIC_REQUESTS support.

Django rolls a request's transaction back when an exception escapes the view.
Hattori turns every failure into a response before Django sees it, so it marks
the rollback itself - for any error response, however it was signalled: raised
or returned, from a view, an auth callback or a permission.
"""

from typing import Literal

import pytest
from django.db import connection
from django.http import HttpResponse
from django.test import Client
from django.urls import path
from someapp.models import Event

from hattori import ApiError, BasePermission, Created, HattoriAPI
from hattori.errors import HttpError
from hattori.security import HttpBearer
from hattori.testing import TestAsyncClient, TestClient

URL = "/api/atomic-requests/"

api = HattoriAPI(urls_namespace="atomic-requests-test")


def write(title: str) -> None:
    Event.objects.create(title=title, start_date="2026-04-23", end_date="2026-04-23")


def written(title: str) -> bool:
    return Event.objects.filter(title=title).exists()


class Conflict(ApiError):
    code = 409
    error_code = "conflict"
    message = "conflict"


class Denied(ApiError):
    code = 401
    error_code = "denied"
    message = "denied"


class NotAllowed(ApiError):
    code = 403
    error_code = "not_allowed"
    message = "not allowed"


class ReturnsDenied(HttpBearer):
    def authenticate(self, request, token: str) -> str | Denied:
        write("auth-returned")
        return Denied()


class RaisesDenied(HttpBearer):
    def authenticate(self, request, token: str) -> str:
        write("auth-raised")
        raise HttpError(401, "denied")


class Declines(HttpBearer):
    def authenticate(self, request, token: str) -> str | None:
        write("auth-declined")
        return None


class Refuses(BasePermission):
    def check(self, request) -> bool:
        write("permission-refused")
        return False


class ReturnsNotAllowed(BasePermission):
    def check(self, request) -> Literal[True] | NotAllowed:
        write("permission-returned")
        return NotAllowed()


class RaisesNotAllowed(BasePermission):
    def check(self, request) -> bool:
        write("permission-raised")
        raise HttpError(403, "not allowed")


@api.post("raised")
def raised(request) -> str:
    write("raised")
    raise HttpError(409, "conflict")


@api.post("returned")
def returned(request) -> str | Conflict:
    write("returned")
    return Conflict()


@api.post("auth-returned", auth=ReturnsDenied())
def auth_returned(request) -> str:
    return "ok"


@api.post("auth-raised", auth=RaisesDenied())
def auth_raised(request) -> str:
    return "ok"


@api.post("auth-declined", auth=Declines())
def auth_declined(request) -> str:
    return "ok"


@api.post("permission-refused", permissions=[Refuses()])
def permission_refused(request) -> str:
    return "ok"


@api.post("permission-returned", permissions=[ReturnsNotAllowed()])
def permission_returned(request) -> str:
    return "ok"


@api.post("permission-raised", permissions=[RaisesNotAllowed()])
def permission_raised(request) -> str:
    return "ok"


@api.post("created")
def created(request) -> Created[str]:
    write("created")
    return Created("ok")


@api.post("raw-response")
def raw_response(request) -> str:
    write("raw-response")
    return HttpResponse(status=409)


@api.get("async")
async def async_view(request) -> str:
    return "ok"


urlpatterns = [
    path("api/atomic-requests/", api.urls),
]


@pytest.fixture(autouse=True)
def urlconf(settings, db):
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.DEBUG = False
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def atomic_requests(monkeypatch):
    monkeypatch.setitem(connection.settings_dict, "ATOMIC_REQUESTS", True)


def post(name: str) -> HttpResponse:
    return Client().post(URL + name, HTTP_AUTHORIZATION="Bearer token")


@pytest.mark.parametrize(
    "name,status",
    [
        ("raised", 409),
        ("returned", 409),
        ("auth-returned", 401),
        ("auth-raised", 401),
        ("auth-declined", 401),
        ("permission-refused", 403),
        ("permission-returned", 403),
        ("permission-raised", 403),
    ],
)
def test_error_response_rolls_back(atomic_requests, name, status):
    response = post(name)

    assert response.status_code == status
    assert not written(name)


def test_success_response_commits(atomic_requests):
    assert post("created").status_code == 201
    assert written("created")


def test_raw_response_keeps_django_semantics(atomic_requests):
    # An HttpResponse is passed through untouched, so Django's own rule applies:
    # only an exception rolls back.
    assert post("raw-response").status_code == 409
    assert written("raw-response")


def test_nothing_rolls_back_without_atomic_requests():
    assert post("returned").status_code == 409
    assert written("returned")


def test_test_client_runs_the_request_transaction(atomic_requests):
    # The rollback lands on the request's transaction, not on one opened
    # further out, such as this test's.
    client = TestClient(api)

    assert client.post("/returned").status_code == 409
    assert not written("returned")

    assert client.post("/created").status_code == 201
    assert written("created")


@pytest.mark.asyncio
async def test_async_test_client_runs_the_request_transaction(atomic_requests):
    with pytest.raises(RuntimeError, match="ATOMIC_REQUESTS with async views"):
        await TestAsyncClient(api).get("/async")
