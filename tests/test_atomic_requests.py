"""ATOMIC_REQUESTS support.

Django rolls a request's transaction back when an exception escapes the view.
Hattori turns every failure into a response before Django sees it, so it marks
the rollback itself.
"""

import pytest
from django.db import connection
from django.http import HttpResponse
from django.test import Client
from django.urls import path
from someapp.models import Event

from hattori import HattoriAPI
from hattori.errors import HttpError
from hattori.testing import TestAsyncClient, TestClient

URL = "/api/atomic-requests/"

api = HattoriAPI(urls_namespace="atomic-requests-test")


def write(title: str) -> None:
    Event.objects.create(title=title, start_date="2026-04-23", end_date="2026-04-23")


def written(title: str) -> bool:
    return Event.objects.filter(title=title).exists()


@api.post("raised")
def raised(request) -> str:
    write("raised")
    raise HttpError(409, "conflict")


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


def test_error_response_rolls_back(atomic_requests):
    response = post("raised")

    assert response.status_code == 409
    assert response.json() == {"detail": "conflict"}
    assert not written("raised")


def test_test_client_runs_the_request_transaction(atomic_requests):
    # The rollback lands on the request's transaction, not on one opened
    # further out, such as this test's.
    assert TestClient(api).post("/raised").status_code == 409
    assert not written("raised")


@pytest.mark.asyncio
async def test_async_test_client_runs_the_request_transaction(atomic_requests):
    with pytest.raises(RuntimeError, match="ATOMIC_REQUESTS with async views"):
        await TestAsyncClient(api).get("/async")
