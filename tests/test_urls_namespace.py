"""APIs mounted side by side must not be handed each other's urls."""

import pytest
from django.core.checks import run_checks
from django.test import Client
from django.urls import include, path, set_urlconf

from hattori import HattoriAPI
from hattori.errors import ConfigError

CLASH = 'mounted at "api/" and "internal/" share the URL namespace "api-1.0.0"'


def make_api(name, **kwargs):
    api = HattoriAPI(**kwargs)

    @api.get(f"/{name}")
    def view(request) -> str:
        return name

    return api


@pytest.fixture
def mount(settings):
    def mount(*urlpatterns):
        # Django takes the patterns themselves in place of a URLconf module.
        settings.ROOT_URLCONF = urlpatterns
        return Client()

    return mount


def namespace_errors():
    return [error.msg for error in run_checks() if error.id == "hattori.E001"]


def schema_paths(client, url):
    return list(client.get(url).json()["paths"])


@pytest.mark.parametrize(
    "url",
    ["/api/openapi.json", "/internal/openapi.json", "/api/docs", "/internal/docs"],
)
def test_apis_sharing_a_namespace_do_not_describe_each_other(mount, url):
    client = mount(
        path("api/", make_api("public").urls),
        path("internal/", make_api("internal").urls),
    )

    with pytest.raises(ConfigError, match=CLASH):
        client.get(url)


def test_shared_namespace_stops_a_schema_built_outside_a_request(mount):
    public, internal = make_api("public"), make_api("internal")
    mount(path("api/", public.urls), path("internal/", internal.urls))

    with pytest.raises(ConfigError, match=CLASH):
        internal.get_openapi_schema()


def test_apis_sharing_a_namespace_still_answer(mount):
    client = mount(
        path("api/", make_api("public").urls),
        path("internal/", make_api("internal").urls),
    )

    assert client.get("/api/public").json() == "public"
    assert client.get("/internal/internal").json() == "internal"


def test_shared_namespace_fails_the_system_check_once(mount):
    mount(
        path("api/", make_api("public").urls),
        path("internal/", make_api("internal").urls),
    )

    (error,) = namespace_errors()
    assert CLASH in error


@pytest.mark.parametrize(
    "kwargs", [{"urls_namespace": "internal"}, {"version": "2.0.0"}]
)
def test_apis_with_their_own_namespace_describe_themselves(mount, kwargs):
    client = mount(
        path("api/", make_api("public").urls),
        path("internal/", make_api("internal", **kwargs).urls),
    )

    assert schema_paths(client, "/api/openapi.json") == ["/api/public"]
    assert schema_paths(client, "/internal/openapi.json") == ["/internal/internal"]
    assert b'"/internal/openapi.json"' in client.get("/internal/docs").content
    assert namespace_errors() == []


def test_one_api_mounted_twice_is_not_a_clash(mount):
    api = make_api("public")
    client = mount(path("api/", api.urls), path("alias/", api.urls))

    assert schema_paths(client, "/alias/openapi.json") == ["/api/public"]
    assert namespace_errors() == []


def test_nested_namespace_clashes_on_the_full_name_only(mount):
    def nested(route, outer, api):
        return path(route, include(([path("api/", api.urls)], "app"), namespace=outer))

    one = make_api("one", urls_namespace="one:api")
    two = make_api("two", urls_namespace="two:api")
    client = mount(nested("one/", "one", one), nested("two/", "two", two))

    assert schema_paths(client, "/two/api/openapi.json") == ["/two/api/two"]
    assert namespace_errors() == []

    twin = make_api("twin", urls_namespace="one:api")
    mount(nested("one/", "one", one), nested("twin/", "one", twin))

    with pytest.raises(ConfigError, match='"one/api/" and "twin/api/"'):
        twin.get_openapi_schema()
    assert len(namespace_errors()) == 1


def test_clash_is_looked_for_in_the_urlconf_in_use(mount):
    public, internal = make_api("public"), make_api("internal")
    mount(path("api/", public.urls))
    assert list(public.get_openapi_schema()["paths"]) == ["/api/public"]

    set_urlconf((path("api/", public.urls), path("internal/", internal.urls)))
    try:
        with pytest.raises(ConfigError, match=CLASH):
            public.get_openapi_schema()
    finally:
        set_urlconf(None)
