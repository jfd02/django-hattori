from datetime import datetime
from http import HTTPStatus
from unittest import mock

import pytest
from django.utils import timezone

from hattori import Router
from hattori.schema import Schema
from hattori.testing import TestClient

router = Router()


@router.get("/request/build_absolute_uri")
def request_build_absolute_uri(request) -> str:
    return request.build_absolute_uri()


@router.get("/request/build_absolute_uri/location")
def request_build_absolute_uri_location(request) -> str:
    return request.build_absolute_uri("location")


@router.get("/test")
def simple_get(request) -> str:
    return "test"


@router.get("/test-headers")
def get_headers(request) -> dict[str, str]:
    return dict(request.headers)


@router.get("/test-cookies")
def get_cookies(request) -> dict[str, str]:
    return dict(request.COOKIES)


@router.get("/test-remote-addr")
def get_remote_addr(request) -> str:
    return request.META.get("REMOTE_ADDR", "")


@router.get("/test-attr")
def get_attr(request) -> str:
    return getattr(request, "trace_id", "")


client = TestClient(router)


def test_meta_override_is_merged_onto_request():
    r = client.get("/test-remote-addr", META={"REMOTE_ADDR": "9.9.9.9"})
    assert r.json() == "9.9.9.9"


def test_arbitrary_kwarg_is_set_on_request():
    r = client.get("/test-attr", trace_id="abc-123")
    assert r.json() == "abc-123"


def test_response_data_caches_null_body():
    from hattori.testing.client import HattoriTestResponse

    fake = mock.Mock(streaming=False, status_code=200, content=b"null")
    response = HattoriTestResponse(fake)

    assert response.data is None  # JSON null -> None, then cached
    with mock.patch.object(response, "json", side_effect=AssertionError):
        assert response.data is None  # served from cache, json() not re-called


@pytest.mark.parametrize(
    "path,expected_status,expected_response",
    [
        ("/request/build_absolute_uri", HTTPStatus.OK, "http://testlocation/"),
        (
            "/request/build_absolute_uri/location",
            HTTPStatus.OK,
            "http://testlocation/location",
        ),
    ],
)
def test_sync_build_absolute_uri(path, expected_status, expected_response):
    response = client.get(path)

    assert response.status_code == expected_status
    assert response.json() == expected_response


class ClientTestSchema(Schema):
    time: datetime


def test_schema_as_data():
    schema_instance = ClientTestSchema(time=timezone.now().replace(microsecond=0))

    with mock.patch.object(client, "_call") as call:
        client.post("/test", json=schema_instance)
        request = call.call_args[0][1]
        assert (
            ClientTestSchema.model_validate_json(request.body).model_dump_json()
            == schema_instance.model_dump_json()
        )


def test_json_as_body():
    schema_instance = ClientTestSchema(time=timezone.now().replace(microsecond=0))

    with mock.patch.object(client, "_call") as call:
        client.post(
            "/test",
            data=schema_instance.model_dump_json(),
            content_type="application/json",
        )
        request = call.call_args[0][1]
        assert (
            ClientTestSchema.model_validate_json(request.body).model_dump_json()
            == schema_instance.model_dump_json()
        )


headered_client = TestClient(router, headers={"A": "a", "B": "b"})


def test_client_request_only_header():
    r = client.get("/test-headers", headers={"A": "na"})
    assert r.json() == {"A": "na"}


def test_headered_client_request_with_default_headers():
    r = headered_client.get("/test-headers")
    assert r.json() == {"A": "a", "B": "b"}


def test_headered_client_request_with_overwritten_and_additional_headers():
    r = headered_client.get("/test-headers", headers={"A": "na", "C": "nc"})
    assert r.json() == {"A": "na", "B": "b", "C": "nc"}


cookied_client = TestClient(router, COOKIES={"A": "a", "B": "b"})


def test_client_request_only_cookies():
    r = client.get("/test-cookies", COOKIES={"A": "na"})
    assert r.json() == {"A": "na"}


def test_headered_client_request_with_default_cookies():
    r = cookied_client.get("/test-cookies")
    assert r.json() == {"A": "a", "B": "b"}


def test_headered_client_request_with_overwritten_and_additional_cookies():
    r = cookied_client.get("/test-cookies", COOKIES={"A": "na", "C": "nc"})
    assert r.json() == {"A": "na", "B": "b", "C": "nc"}


def test_request_session_and_resolver_match():
    router = Router()

    @router.get("/items/{int:item_id}", url_name="item-detail")
    def item(request, item_id: int) -> dict:
        previous = dict(request.session)
        request.session["seen"] = item_id
        return {
            "previous": previous,
            "name": request.resolver_match.url_name,
            "kwargs": request.resolver_match.kwargs,
        }

    client = TestClient(router)
    for _ in range(2):
        assert client.get("/items/3").json() == {
            "previous": {},
            "name": "item-detail",
            "kwargs": {"item_id": 3},
        }
    session = {"token": "secret"}
    assert client.get("/items/4", session=session).json()["previous"] == {
        "token": "secret"
    }
    assert session == {"token": "secret", "seen": 4}


@pytest.mark.asyncio
async def test_async_request_user_and_overrides():
    from django.contrib.auth.models import AnonymousUser

    from hattori.testing import TestAsyncClient

    router = Router()

    @router.get("/user", url_name="current-user")
    async def user(request) -> dict:
        resolved_user = await request.auser()
        return {
            "same": resolved_user is request.user,
            "anonymous": resolved_user.is_anonymous,
            "session": request.session,
            "name": request.resolver_match.url_name,
        }

    client = TestAsyncClient(router)
    assert (await client.get("/user")).json() == {
        "same": True,
        "anonymous": True,
        "session": {},
        "name": "current-user",
    }
    supplied_user = mock.Mock(is_anonymous=False)
    response = await client.get(
        "/user", user=supplied_user, session={"token": "secret"}
    )
    assert response.json() == {
        "same": True,
        "anonymous": False,
        "session": {"token": "secret"},
        "name": "current-user",
    }

    async def custom_auser():
        return AnonymousUser()

    response = await client.get("/user", user=supplied_user, auser=custom_auser)
    assert response.json()["same"] is False
    assert response.json()["anonymous"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
async def test_async_client_methods_preserve_request_options(method):
    from hattori.testing import TestAsyncClient

    router = Router()

    @router.api_operation([method.upper()], "/echo")
    async def echo(request) -> dict:
        return {
            "method": request.method,
            "body": request.body.decode(),
            "query": request.GET.get("q"),
            "cookie": request.COOKIES["cookie"],
            "header": request.headers["X-Test"],
        }

    client = TestAsyncClient(
        router, headers={"X-Test": "default"}, COOKIES={"cookie": "value"}
    )
    expected = {
        "method": method.upper(),
        "body": '{"value":1}',
        "query": "search",
        "cookie": "value",
        "header": "override",
    }
    options = {
        "json": {"value": 1},
        "query_params": {"q": "search"},
        "headers": {"X-Test": "override"},
    }
    assert (await getattr(client, method)("/echo", **options)).json() == expected
    assert (await client.request(method.upper(), "/echo", **options)).json() == expected
