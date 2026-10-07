"""The API's own root URL answers with the API's 404, not Django's page."""

import pytest
from django.http import Http404
from django.test import Client
from django.urls import path

from hattori import HattoriAPI

api = HattoriAPI(urls_namespace="api-root-test")
echoing = HattoriAPI(urls_namespace="api-root-echo")


@echoing.exception_handler(Http404)
def echo(request, exc):
    return echoing.create_response(request, {"message": str(exc)}, status=404)


urlpatterns = [
    path("api/", api.urls),
    path("echo/", echoing.urls),
]


@pytest.fixture
def client(settings):
    settings.ROOT_URLCONF = __name__
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.DEBUG = False
    return Client()


def test_api_root_answers_with_the_api_404(client):
    response = client.get("/api/")

    assert response.status_code == 404
    assert response["Content-Type"] == "application/json; charset=utf-8"
    assert response.json() == {"detail": "Not Found"}


def test_api_root_names_the_docs_url_in_debug(client, settings):
    settings.DEBUG = True

    assert client.get("/api/").json() == {"detail": "Not Found: docs_url = /api/docs"}


def test_api_root_hint_stays_out_of_a_production_handler(client):
    assert client.get("/echo/").json() == {"message": ""}


def test_unmatched_path_under_the_api_is_still_django_s_to_answer(client):
    response = client.get("/api/nope")

    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/html")
