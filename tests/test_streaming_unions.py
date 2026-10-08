"""A streaming operation declares its other responses beside the stream, in a
union, and answers with one by returning it before anything is streamed."""

from typing import Union

import pytest
from django.http import HttpResponse

from hattori import JSONL, SSE, ApiError, HattoriAPI, Schema
from hattori.errors import ConfigError
from hattori.testing import TestAsyncClient, TestClient


class Item(Schema):
    name: str


class Missing(ApiError):
    code = 404
    error_code = "missing"
    message = "missing"


api = HattoriAPI()


@api.get("/generator/{name}")
def generator(request, name: str) -> JSONL[Item] | Missing:
    if name == "none":
        return Missing()  # noqa: B901
    yield Item(name=name)


@api.get("/returned/{name}")
def returned(request, name: str) -> Missing | JSONL[Item]:
    if name == "none":
        return Missing()
    return (Item(name=each) for each in [name])


@api.get("/spelled-out/{name}")
def spelled_out(request, name: str) -> Union[JSONL[Item], Missing]:  # noqa: UP007
    if name == "none":
        return Missing()  # noqa: B901
    yield Item(name=name)


@api.get("/raw/{name}")
def raw(request, name: str) -> JSONL[Item] | Missing:
    if name == "none":
        return HttpResponse(  # noqa: B901
            "gone", status=410, content_type="text/plain"
        )
    yield Item(name=name)


@api.get("/late/{name}")
def late(request, name: str) -> JSONL[Item] | Missing:
    yield Item(name=name)
    return Missing()  # noqa: B901


@api.get("/async-returned/{name}")
async def async_returned(request, name: str) -> SSE[Item] | Missing:
    if name == "none":
        return Missing()

    async def items():
        yield Item(name=name)

    return items()


@api.get("/async-generator/{name}")
async def async_generator(request, name: str) -> Missing | JSONL[Item]:
    yield Item(name=name)


client = TestClient(api)

SYNC_PATHS = ["/generator", "/returned", "/spelled-out"]


@pytest.mark.parametrize("path", SYNC_PATHS)
def test_stream_is_streamed(path):
    response = client.get(f"{path}/a")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/jsonl"
    assert response.content == b'{"name":"a"}\n'


@pytest.mark.parametrize("path", SYNC_PATHS)
def test_other_response_is_sent_as_any_operation_sends_it(path):
    response = client.get(f"{path}/none")

    assert response.status_code == 404
    assert response["Content-Type"] == "application/json; charset=utf-8"
    assert response.json() == {"code": "missing", "message": "missing"}


def test_response_returned_outright_is_sent_as_it_is():
    response = client.get("/raw/none")

    assert response.status_code == 410
    assert response.content == b"gone"
    assert client.get("/raw/a").content == b'{"name":"a"}\n'


def test_what_a_generator_returns_after_it_has_yielded_is_not_sent():
    # The status line and headers left with the first item.
    response = client.get("/late/a")

    assert response.status_code == 200
    assert response.content == b'{"name":"a"}\n'


@pytest.mark.asyncio
async def test_async_view_returns_its_stream_or_another_response():
    async_client = TestAsyncClient(api)

    streamed = await async_client.get("/async-returned/a")
    assert streamed.status_code == 200
    assert streamed["Content-Type"] == "text/event-stream"
    assert streamed.content == b'data: {"name":"a"}\n\n'

    missing = await async_client.get("/async-returned/none")
    assert missing.status_code == 404
    assert missing.json() == {"code": "missing", "message": "missing"}


@pytest.mark.asyncio
async def test_async_generator_still_streams_beside_a_declared_response():
    response = await TestAsyncClient(api).get("/async-generator/a")

    assert response.status_code == 200
    assert response.content == b'{"name":"a"}\n'


@pytest.mark.parametrize(
    "path,media_type",
    [
        ("/generator/{name}", "application/jsonl"),
        # The stream is the stream wherever in the union it stands.
        ("/returned/{name}", "application/jsonl"),
        ("/spelled-out/{name}", "application/jsonl"),
        ("/async-returned/{name}", "text/event-stream"),
        ("/async-generator/{name}", "application/jsonl"),
    ],
)
def test_spec_documents_the_stream_and_the_other_response_each_as_sent(
    path, media_type
):
    responses = api.get_openapi_schema()["paths"][f"/api{path}"]["get"]["responses"]

    assert list(responses[200]["content"]) == [media_type]
    assert responses[404]["content"] == {
        "application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}
    }


def test_two_streams_in_one_return_type_are_rejected():
    api = HattoriAPI()

    with pytest.raises(ConfigError, match="more than one stream"):

        @api.get("/both")
        def both(request) -> JSONL[Item] | SSE[Item]:
            yield Item(name="a")


@pytest.mark.parametrize("other", [Item, None])
def test_stream_and_another_response_for_one_status_are_rejected(other):
    api = HattoriAPI()

    def ambiguous(request):
        yield Item(name="a")

    ambiguous.__annotations__["return"] = JSONL[Item] | other

    with pytest.raises(ConfigError, match="both a stream and another response"):
        api.get("/ambiguous")(ambiguous)
