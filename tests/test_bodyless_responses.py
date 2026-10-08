"""A response with no body names no media type for one."""

import pytest
from django.http import HttpResponse

from hattori import HattoriAPI, NoContent, Schema
from hattori.testing import TestAsyncClient, TestClient


class Item(Schema):
    a: int


api = HattoriAPI()


@api.get("/none")
def none(request) -> None:
    return None


@api.delete("/gone")
def gone(request) -> NoContent:
    return NoContent()


@api.get("/maybe")
def maybe(request) -> Item | None:
    return None


@api.get("/kept")
def kept(request, response: HttpResponse) -> None:
    response["X-Trace"] = "abc"
    response.set_cookie("seen", "1")


@api.get("/written")
def written(request, response: HttpResponse) -> None:
    response.write("a,b\n1,2\n")


@api.get("/written-as-csv")
def written_as_csv(request, response: HttpResponse) -> None:
    response["Content-Type"] = "text/csv"
    response.write("a,b\n1,2\n")


@api.get("/async-written-as-csv")
async def async_written_as_csv(request, response: HttpResponse) -> None:
    response["Content-Type"] = "text/csv"
    response.write("a,b\n1,2\n")


@api.get("/async-none")
async def async_none(request) -> None:
    return None


client = TestClient(api)


@pytest.mark.parametrize(
    "method,path,status", [("GET", "/none", 200), ("DELETE", "/gone", 204)]
)
def test_bodyless_response_has_no_content_type(method, path, status):
    response = client.request(method, path)

    assert response.status_code == status
    assert response.content == b""
    assert not response.has_header("Content-Type")


@pytest.mark.asyncio
async def test_bodyless_response_of_an_async_view_has_no_content_type():
    response = await TestAsyncClient(api).get("/async-none")

    assert response.status_code == 200
    assert response.content == b""
    assert not response.has_header("Content-Type")


def test_what_the_view_set_on_the_response_is_kept():
    response = client.get("/kept")

    assert response["X-Trace"] == "abc"
    assert response.cookies["seen"].value == "1"
    assert not response.has_header("Content-Type")


def test_body_the_view_wrote_itself_keeps_its_content_type():
    default = client.get("/written")
    assert default.content == b"a,b\n1,2\n"
    assert default["Content-Type"] == "application/json; charset=utf-8"

    as_csv = client.get("/written-as-csv")
    assert as_csv.content == b"a,b\n1,2\n"
    assert as_csv["Content-Type"] == "text/csv"


@pytest.mark.asyncio
async def test_body_an_async_view_wrote_itself_keeps_its_content_type():
    response = await TestAsyncClient(api).get("/async-written-as-csv")

    assert response.content == b"a,b\n1,2\n"
    assert response["Content-Type"] == "text/csv"


def test_null_body_is_still_json():
    response = client.get("/maybe")

    assert response.status_code == 200
    assert response.content == b"null"
    assert response["Content-Type"].startswith("application/json")


def test_spec_documents_no_content_for_either():
    paths = api.get_openapi_schema()["paths"]

    assert paths["/api/none"]["get"]["responses"] == {200: {"description": "OK"}}
    assert paths["/api/gone"]["delete"]["responses"] == {
        204: {"description": "No Content"}
    }
