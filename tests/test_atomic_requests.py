import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client
from django.urls import path
from someapp.models import Event

from hattori import HattoriAPI
from hattori.errors import HttpError
from hattori.security import BasePermission

api = HattoriAPI(urls_namespace="atomic-requests-test")


@api.post("httperror")
def raise_http_error(request) -> str:
    Event.objects.create(
        title="atomic-request-rollback",
        start_date="2026-04-23",
        end_date="2026-04-23",
    )
    raise HttpError(409, "conflict")


def _create_event() -> None:
    Event.objects.create(
        title="atomic-request-rollback",
        start_date="2026-04-23",
        end_date="2026-04-23",
    )


def writes_then_denies(request):
    _create_event()
    raise PermissionDenied


class WritesThenDenies(BasePermission):
    def check(self, request) -> bool:
        _create_event()
        raise PermissionDenied


def writes_then_declines(request):
    _create_event()
    return None


@api.post("auth-raises", auth=writes_then_denies)
def auth_raises(request) -> str:
    return "unreachable"


@api.post("permission-raises", permissions=[WritesThenDenies()])
def permission_raises(request) -> str:
    return "unreachable"


@api.post("auth-declines", auth=writes_then_declines)
def auth_declines(request) -> str:
    return "unreachable"


urlpatterns = [
    path("api/atomic-requests/", api.urls),
]


@pytest.mark.django_db
def test_atomic_requests_rolls_back_http_errors(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.DEBUG = False
    settings.ROOT_URLCONF = __name__

    previous_atomic_requests = connection.settings_dict.get("ATOMIC_REQUESTS")
    connection.settings_dict["ATOMIC_REQUESTS"] = True
    Event.objects.filter(title="atomic-request-rollback").delete()

    try:
        response = Client().post("/api/atomic-requests/httperror")

        assert response.status_code == 409
        assert response.json() == {"detail": "conflict"}
        assert Event.objects.filter(title="atomic-request-rollback").count() == 0
    finally:
        Event.objects.filter(title="atomic-request-rollback").delete()
        if previous_atomic_requests is None:
            connection.settings_dict.pop("ATOMIC_REQUESTS", None)
        else:
            connection.settings_dict["ATOMIC_REQUESTS"] = previous_atomic_requests


@pytest.fixture
def atomic_requests(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.DEBUG = False
    settings.ROOT_URLCONF = __name__
    previous = connection.settings_dict.get("ATOMIC_REQUESTS")
    connection.settings_dict["ATOMIC_REQUESTS"] = True
    Event.objects.filter(title="atomic-request-rollback").delete()
    try:
        yield
    finally:
        Event.objects.filter(title="atomic-request-rollback").delete()
        if previous is None:
            connection.settings_dict.pop("ATOMIC_REQUESTS", None)
        else:
            connection.settings_dict["ATOMIC_REQUESTS"] = previous


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["auth-raises", "permission-raises"])
def test_atomic_requests_rolls_back_what_auth_wrote_before_raising(
    atomic_requests, route
):
    # Django would have rolled back had the exception reached it.
    response = Client().post(f"/api/atomic-requests/{route}")

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert Event.objects.filter(title="atomic-request-rollback").count() == 0


@pytest.mark.django_db
def test_atomic_requests_keeps_what_auth_wrote_before_declining(atomic_requests):
    # Nothing was raised, so as far as Django is concerned the request succeeded.
    response = Client().post("/api/atomic-requests/auth-declines")

    assert response.status_code == 401
    assert Event.objects.filter(title="atomic-request-rollback").count() == 1
