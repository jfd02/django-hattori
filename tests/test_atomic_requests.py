"""ATOMIC_REQUESTS support.

Django rolls a request's transaction back when an exception escapes the view.
Hattori turns every failure into a response before Django sees it, so it marks
the rollback itself - for any error response, however it was signalled: raised
or returned, from a view, an auth callback or a permission.
"""

from functools import wraps
from typing import Literal

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.http import HttpResponse
from django.test import Client
from django.urls import path, resolve
from someapp.models import Event

from hattori import ApiError, BasePermission, Created, HattoriAPI
from hattori.decorators import decorate_view
from hattori.errors import ConfigError, HttpError
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


class RaisesDjangoDenied(HttpBearer):
    def authenticate(self, request, token: str) -> str:
        write("auth-django-raised")
        raise PermissionDenied


class RaisesDjangoNotAllowed(BasePermission):
    def check(self, request) -> bool:
        write("permission-django-raised")
        raise PermissionDenied


class ReturnsUnrun(HttpBearer):
    def authenticate(self, request, token: str):
        write("auth-unrun")
        return (principal for principal in [token])


class ReturnsUnrunCheck(BasePermission):
    def check(self, request):
        write("permission-unrun")
        return (verdict for verdict in [True])


class ReturnsNoVerdict(BasePermission):
    def check(self, request):
        write("permission-no-verdict")
        return "allowed"


def writes_then_denies(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        write("decorator-raised")
        raise PermissionDenied

    return wrapper


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


@api.post("django-raised")
def django_raised(request) -> str:
    write("django-raised")
    raise PermissionDenied


@api.post("auth-django-raised", auth=RaisesDjangoDenied())
def auth_django_raised(request) -> str:
    return "ok"


@api.post("permission-django-raised", permissions=[RaisesDjangoNotAllowed()])
def permission_django_raised(request) -> str:
    return "ok"


@api.post("decorator-raised")
@decorate_view(writes_then_denies)
def decorator_raised(request) -> str:
    return "ok"


@api.post("auth-unrun", auth=ReturnsUnrun())
def auth_unrun(request) -> str:
    return "ok"


@api.post("permission-unrun", permissions=[ReturnsUnrunCheck()])
def permission_unrun(request) -> str:
    return "ok"


@api.post("permission-no-verdict", permissions=[ReturnsNoVerdict()])
def permission_no_verdict(request) -> str:
    return "ok"


@api.post("created")
def created(request) -> Created[str]:
    write("created")
    return Created("ok")


@api.post("raw-response")
def raw_response(request) -> str:
    write("raw-response")
    return HttpResponse(status=409)


@api.post("non-atomic")
@transaction.non_atomic_requests
def non_atomic(request) -> str | Conflict:
    write("non-atomic")
    return Conflict()


@transaction.non_atomic_requests
@api.post("non-atomic-outermost")
def non_atomic_outermost(request) -> str:
    return "ok"


@api.post("other-db-non-atomic")
@transaction.non_atomic_requests(using="other")
def other_db_non_atomic(request) -> str | Conflict:
    write("other-db-non-atomic")
    return Conflict()


@api.get("shared")
@transaction.non_atomic_requests
def shared_get(request) -> str:
    return "ok"


@api.post("shared")
def shared_post(request) -> str | Conflict:
    write("shared")
    return Conflict()


@api.get("async")
async def async_view(request) -> str:
    return "ok"


@api.get("async-non-atomic")
@transaction.non_atomic_requests
async def async_non_atomic(request) -> str | Conflict:
    return Conflict()


# An API whose handler writes, for errors no endpoint is involved in.
recording = HattoriAPI(urls_namespace="atomic-requests-recording")


@recording.exception_handler(HttpError)
def record(request, exc):
    write(f"handler-{exc.status_code}")
    return recording.create_response(request, {}, status=exc.status_code)


@recording.get("thing")
def thing(request) -> str:
    return "ok"


# An API that answers for a check whose result was rejected.
answering = HattoriAPI(urls_namespace="atomic-requests-answering")


@answering.exception_handler(ConfigError)
def answer(request, exc):
    return answering.create_response(request, {}, status=500)


answering.post("auth-unrun", auth=ReturnsUnrun())(auth_unrun)
answering.post("permission-unrun", permissions=[ReturnsUnrunCheck()])(permission_unrun)
answering.post("permission-no-verdict", permissions=[ReturnsNoVerdict()])(
    permission_no_verdict
)


urlpatterns = [
    path("api/atomic-requests/", api.urls),
    path("api/atomic-recording/", recording.urls),
    path("api/atomic-answering/", answering.urls),
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
        # A Django exception the API answers in Django's place, wherever raised.
        ("django-raised", 403),
        ("auth-django-raised", 403),
        ("permission-django-raised", 403),
        ("decorator-raised", 403),
        # Opting out for another database leaves this one's request atomic.
        ("other-db-non-atomic", 409),
        # GET opted out but POST did not, and Django decides per URL.
        ("shared", 409),
    ],
)
def test_error_response_rolls_back(atomic_requests, name, status):
    response = post(name)

    assert response.status_code == status
    assert not written(name)


def test_error_the_framework_answers_itself_rolls_back(atomic_requests):
    # No endpoint ran, but it is an error response all the same.
    assert Client().put("/api/atomic-recording/thing").status_code == 405
    assert not written("handler-405")

    assert Client().get("/api/atomic-recording/").status_code == 404
    assert not written("handler-404")


REJECTED = ["auth-unrun", "permission-unrun", "permission-no-verdict"]


@pytest.mark.parametrize("name", REJECTED)
def test_rejected_check_result_rolls_back(atomic_requests, name):
    # Nothing answers the ConfigError, so it reaches Django, which rolls back.
    with pytest.raises(ConfigError):
        post(name)

    assert not written(name)


@pytest.mark.parametrize("name", REJECTED)
def test_rejected_check_result_a_handler_answers_rolls_back(atomic_requests, name):
    response = Client().post(
        "/api/atomic-answering/" + name, HTTP_AUTHORIZATION="Bearer token"
    )

    assert response.status_code == 500
    assert not written(name)


@pytest.mark.parametrize("name", REJECTED)
def test_rejected_check_result_keeps_its_write_without_atomic_requests(name):
    with pytest.raises(ConfigError):
        post(name)

    assert written(name)


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


def test_non_atomic_requests_marker_reaches_the_url_callback():
    def marker(name: str) -> set[str] | None:
        return getattr(resolve(URL + name).func, "_non_atomic_requests", None)

    assert marker("non-atomic") == {"default"}
    assert marker("non-atomic-outermost") == {"default"}
    assert marker("other-db-non-atomic") == {"other"}
    assert marker("async-non-atomic") == {"default"}
    assert marker("returned") is None
    assert marker("shared") is None


def test_non_atomic_view_keeps_its_writes(atomic_requests):
    # The view opted out of the request transaction, so there is nothing of
    # hattori's to roll back - least of all a transaction opened further out,
    # such as this test's.
    assert post("non-atomic").status_code == 409
    assert written("non-atomic")


def test_async_view_runs_once_it_opts_out(atomic_requests):
    with pytest.raises(RuntimeError, match="ATOMIC_REQUESTS with async views"):
        Client().get(URL + "async")

    assert Client().get(URL + "async-non-atomic").status_code == 409


def test_test_client_runs_the_request_transaction(atomic_requests):
    # The rollback lands on the request's transaction, not on one opened
    # further out, such as this test's.
    client = TestClient(api)

    assert client.post("/returned").status_code == 409
    assert not written("returned")

    assert client.post("/created").status_code == 201
    assert written("created")

    assert client.post("/non-atomic").status_code == 409
    assert written("non-atomic")


@pytest.mark.asyncio
async def test_async_test_client_runs_the_request_transaction(atomic_requests):
    client = TestAsyncClient(api)

    with pytest.raises(RuntimeError, match="ATOMIC_REQUESTS with async views"):
        await client.get("/async")

    assert (await client.get("/async-non-atomic")).status_code == 409
