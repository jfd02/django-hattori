"""Paths under an API that no route matches.

The API's own root always answers with the API's 404. Everything else under the
mount is Django's to answer unless the API is built with ``catch_all=True``,
which claims the whole mount, so it is opt-in.
"""

import pytest
from django.test import Client
from django.urls import path, reverse

from hattori import HattoriAPI
from hattori.testing import TestClient


def _api(namespace: str, **kwargs) -> HattoriAPI:
    api = HattoriAPI(urls_namespace=namespace, **kwargs)

    @api.get("/items")
    def items(request) -> str:
        return namespace

    @api.get("/slashed/")
    def slashed(request) -> str:
        return namespace

    @api.post("/slashed/")
    def create_slashed(request) -> str:
        return namespace

    return api


plain = _api("catch-all-off")
owned = _api("catch-all-on", catch_all=True)
nested = _api("catch-all-nested", catch_all=True)
shadowed = _api("catch-all-shadowed")

site = HattoriAPI(urls_namespace="catch-all-site-root", catch_all=True)


@site.get("/{path:anything}/")
def anything(request, anything: str) -> str:
    return anything


urlpatterns = [
    path("plain/", plain.urls),
    path("owned/nested/", nested.urls),
    path("owned/", owned.urls),
    path("owned/late/", shadowed.urls),
]


class SiteRoot:
    """A URLconf of its own: mounted at the root, the catch-all owns the site."""

    urlpatterns = [path("", site.urls)]


NOT_FOUND = {"detail": "Not Found"}


@pytest.fixture
def client(settings):
    settings.ROOT_URLCONF = __name__
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.DEBUG = False
    return Client(enforce_csrf_checks=True)


def test_api_root_answers_with_the_api_404(client):
    response = client.get("/plain/")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_api_root_names_the_docs_url_in_debug(client, settings):
    settings.DEBUG = True

    assert client.get("/plain/").json() == {
        "detail": "Not Found: docs_url = /plain/docs"
    }


def test_api_root_hint_stays_out_of_a_production_handler():
    from django.http import Http404
    from django.test import RequestFactory

    api = _api("catch-all-echo")

    @api.exception_handler(Http404)
    def echo(request, exc):
        return api.create_response(request, {"message": str(exc)}, status=404)

    root = api.urls[0][-1].callback

    assert root(RequestFactory().get("/")).content == b'{"message":""}'


def test_api_root_is_csrf_exempt_like_every_operation(client):
    response = client.post("/plain/")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_unmatched_path_is_left_to_django_by_default(client):
    response = client.get("/plain/nope")

    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/html")


@pytest.mark.parametrize("method", ["get", "post", "put", "delete"])
@pytest.mark.parametrize("url", ["/owned/nope", "/owned/items/1/deeper/"])
def test_catch_all_answers_unmatched_paths_with_the_api_404(client, method, url):
    response = getattr(client, method)(url)

    assert response.status_code == 404
    assert response["Content-Type"] == "application/json; charset=utf-8"
    assert response.json() == NOT_FOUND


def test_catch_all_leaves_reversing_and_the_schema_alone(client):
    # reverse() has to be able to read every pattern in the namespace.
    assert reverse("catch-all-on:items") == "/owned/items"
    assert reverse("catch-all-on:api-root") == "/owned/"
    assert "/owned/items" in client.get("/owned/openapi.json").json()["paths"]
    assert client.get("/owned/docs").status_code == 200


def test_catch_all_leaves_real_routes_alone(client):
    assert client.get("/owned/items").json() == "catch-all-on"
    assert client.get("/owned/slashed/").json() == "catch-all-on"


@pytest.mark.parametrize("prefix", ["plain", "owned"])
def test_append_slash_redirect_is_the_same_with_and_without_catch_all(client, prefix):
    response = client.get(f"/{prefix}/slashed?page=2")

    assert response.status_code == 301
    assert response["Location"] == f"/{prefix}/slashed/?page=2"


def test_catch_all_does_not_redirect_onto_itself(client):
    assert client.get("/owned/nope").status_code == 404


def test_catch_all_reads_a_path_holding_a_newline_whole(client):
    # Not as "slashed", the part before the newline, which would redirect.
    assert client.get("/owned/slashed%0Ax").json() == NOT_FOUND


def test_slash_redirect_cannot_leave_the_site(client, settings):
    # A scheme-relative target would be an open redirect; CommonMiddleware
    # escapes it, and so must the redirect sent in its place.
    settings.ROOT_URLCONF = SiteRoot

    # Set directly: the test client would read the host out of such a URL.
    response = client.get("/", PATH_INFO="//evil.example/x")

    assert response.status_code == 301
    assert response["Location"] == "/%2Fevil.example/x/"


@pytest.mark.parametrize("prefix", ["plain", "owned"])
def test_slash_redirect_of_a_request_with_a_body_is_refused_in_debug(
    client, settings, prefix
):
    # Django refuses rather than silently drop the body; the same with catch_all.
    settings.DEBUG = True

    with pytest.raises(RuntimeError, match="APPEND_SLASH"):
        client.post(f"/{prefix}/slashed")


def test_catch_all_ignores_append_slash_when_it_is_off(client, settings):
    settings.APPEND_SLASH = False

    assert client.get("/owned/slashed").json() == NOT_FOUND


def test_a_mount_listed_before_the_catch_all_is_still_served(client):
    assert client.get("/owned/nested/items").json() == "catch-all-nested"
    assert client.get("/owned/nested/nope").json() == NOT_FOUND


def test_a_mount_listed_after_the_catch_all_is_shadowed(client):
    # The reason catch_all is opt-in: the API owns every URL beneath its mount.
    assert client.get("/owned/late/items").json() == NOT_FOUND


def test_test_client_resolves_unmatched_paths_only_with_catch_all():
    assert TestClient(owned).get("/nope").json() == NOT_FOUND
    with pytest.raises(Exception, match="Cannot resolve"):
        TestClient(plain).get("/nope")


def test_test_client_sees_the_same_slash_redirect_as_the_site():
    # Decided by the API's own routes, not by whatever the project routes.
    response = TestClient(owned).get("/slashed")

    assert response.status_code == 301
    assert response["Location"] == "/slashed/"
    assert TestClient(owned).get("/admin").json() == NOT_FOUND
