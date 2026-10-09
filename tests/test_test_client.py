import threading
from datetime import datetime
from http import HTTPStatus
from unittest import mock

import pytest
from django.http import QueryDict, StreamingHttpResponse
from django.utils import timezone
from django.utils.asyncio import async_unsafe

from hattori import JSONL, Router
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


echo_router = Router()


@echo_router.api_operation(["GET", "POST"], "/echo")
def echo(request) -> dict:
    return {
        "content_type": request.content_type,
        "GET": {key: request.GET.getlist(key) for key in request.GET},
        "POST": {key: request.POST.getlist(key) for key in request.POST},
    }


@echo_router.get("/async")
async def async_echo(request) -> dict:
    return {"GET": dict(request.GET.items())}


@echo_router.get("/async-stream")
async def async_stream(request) -> JSONL[int]:
    for number in range(3):
        yield number


@echo_router.get("/sync-stream")
def sync_stream(request) -> JSONL[int]:
    yield from range(3)


@echo_router.get("/sync-view-async-stream")
def sync_view_async_stream(request) -> str:
    async def chunks():
        yield b"a"
        yield b"b"

    return StreamingHttpResponse(chunks())


echo_client = TestClient(echo_router)


def test_json_is_sent_as_json():
    assert echo_client.post("/echo", json={"a": 1}).json()["content_type"] == (
        "application/json"
    )


def test_explicit_content_type_wins_over_the_json_default():
    response = echo_client.post("/echo", json={"a": 1}, content_type="text/plain")
    assert response.json()["content_type"] == "text/plain"


def test_query_params_are_merged_into_the_query_of_the_path():
    response = echo_client.get(
        "/echo?a=1&a=2&b=3", query_params={"b": "4", "c": ["5", "6"]}
    )
    assert response.json()["GET"] == {"a": ["1", "2"], "b": ["4"], "c": ["5", "6"]}


@pytest.mark.parametrize("emptied", [{"a": []}, QueryDict("", mutable=True)])
def test_empty_list_removes_the_key_from_the_query_of_the_path(emptied):
    if isinstance(emptied, QueryDict):
        emptied.setlist("a", [])
    response = echo_client.get("/echo?a=old&b=kept", query_params=emptied)
    assert response.json()["GET"] == {"b": ["kept"]}


def test_query_emptied_of_every_key_leaves_no_question_mark():
    with mock.patch.object(echo_client, "_call") as call:
        echo_client.get("/echo?a=old", query_params={"a": []})
        assert call.call_args[0][1].get_full_path() == "/echo"


def test_path_query_is_left_as_written_when_nothing_is_merged():
    with mock.patch.object(echo_client, "_call") as call:
        echo_client.get("/echo?b&a=%20")
        assert call.call_args[0][1].get_full_path() == "/echo?b&a=%20"


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_data_of_a_get_is_its_query(method):
    with mock.patch.object(echo_client, "_call") as call:
        echo_client.request(method, "/echo?a=0", data={"a": "1", "b": ["2", "3"]})
        request = call.call_args[0][1]
    assert {key: request.GET.getlist(key) for key in request.GET} == {
        "a": ["1"],
        "b": ["2", "3"],
    }
    assert not request.POST


def test_querydict_given_to_a_get_keeps_its_repeated_keys():
    response = echo_client.get("/echo", data=QueryDict("a=1&a=2"))
    assert response.json()["GET"] == {"a": ["1", "2"]}


def test_query_params_win_over_the_data_of_a_get():
    response = echo_client.get(
        "/echo", data={"a": "1", "b": "2"}, query_params={"a": "9"}
    )
    assert response.json()["GET"] == {"a": ["9"], "b": ["2"]}
    assert response.json()["POST"] == {}


def test_data_of_a_post_is_still_its_form():
    response = echo_client.post("/echo?a=1", data={"b": "2", "c": ["3", "4"]})
    assert response.json()["GET"] == {"a": ["1"]}
    assert response.json()["POST"] == {"b": ["2"], "c": ["3", "4"]}


def test_sync_client_runs_an_async_view():
    response = echo_client.get("/async", query_params={"a": "1"})
    assert response.status_code == 200
    assert response.json() == {"GET": {"a": "1"}}


def test_sync_client_reads_an_async_stream():
    assert echo_client.get("/async-stream").content == b"0\n1\n2\n"


def test_sync_client_reads_an_async_stream_a_sync_view_returns():
    assert echo_client.get("/sync-view-async-stream").content == b"ab"


@pytest.mark.asyncio
async def test_async_client_reads_an_async_stream_a_sync_view_returns():
    from hattori.testing import TestAsyncClient

    response = await TestAsyncClient(echo_router).get("/sync-view-async-stream")
    assert response.content == b"ab"


@pytest.mark.asyncio
async def test_sync_client_inside_an_event_loop_names_the_async_client(recwarn):
    with pytest.raises(RuntimeError, match="Use TestAsyncClient there"):
        echo_client.get("/async")
    with pytest.raises(RuntimeError, match="Use TestAsyncClient there"):
        echo_client.get("/sync-view-async-stream")

    assert not recwarn


@pytest.mark.asyncio
async def test_async_client_runs_a_sync_view():
    from hattori.testing import TestAsyncClient

    client = TestAsyncClient(echo_router)
    response = await client.post("/echo?a=1", json={"b": 2})
    assert response.status_code == 200
    assert response.json() == {
        "content_type": "application/json",
        "GET": {"a": ["1"]},
        "POST": {},
    }


@pytest.mark.asyncio
async def test_async_client_reads_a_sync_stream_off_the_event_loop():
    from hattori.testing import TestAsyncClient

    router = Router()
    threads = []

    @router.get("/stream")
    def stream(request) -> JSONL[int]:
        threads.append(threading.current_thread())
        yield 1
        threads.append(threading.current_thread())

    response = await TestAsyncClient(router).get("/stream")

    assert response.content == b"1\n"
    assert len(threads) == 2
    assert threading.current_thread() not in threads


@pytest.mark.asyncio
async def test_async_client_reads_a_sync_stream_an_async_view_returns_off_the_loop():
    from hattori.testing import TestAsyncClient

    router = Router()

    @async_unsafe("read on the event loop")
    def chunk():
        return b"a"

    @router.get("/stream")
    async def stream(request) -> str:
        return StreamingHttpResponse(chunk() for _ in range(2))

    assert (await TestAsyncClient(router).get("/stream")).content == b"aa"


def test_sync_client_reads_a_sync_stream_an_async_view_returns():
    router = Router()

    @router.get("/stream")
    async def stream(request) -> str:
        return StreamingHttpResponse(iter([b"a", b"b"]))

    assert TestClient(router).get("/stream").content == b"ab"
