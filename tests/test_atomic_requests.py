import pytest
from django.db import connection
from django.test import Client
from django.urls import path

from hattori import HattoriAPI
from hattori.errors import HttpError
from someapp.models import Event


api = HattoriAPI(urls_namespace="atomic-requests-test")


@api.post("httperror")
def raise_http_error(request) -> str:
    Event.objects.create(
        title="atomic-request-rollback",
        start_date="2026-04-23",
        end_date="2026-04-23",
    )
    raise HttpError(409, "conflict")


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
