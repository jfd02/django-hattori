"""A JSON body is read as JSON only when the request says that is what it is.

A browser sends a form across sites as ``text/plain``, as form data or with no
type at all, cookies included and without a preflight; a JSON type it has to
ask for first. Holding the body to its media type keeps that request off JSON
endpoints.
"""

import pytest
from django.test import Client
from django.urls import path

from hattori import ApiError, Body, Form, HattoriAPI, HttpErrorBody, Schema
from hattori.errors import get_http_error_model, set_http_error_model
from hattori.params.models import is_json_media_type
from hattori.testing import TestClient


class Item(Schema):
    a: int


class WrongType(ApiError):
    code = 415
    error_code = "wrong_type"
    message = "Only JSON is taken here"


api = HattoriAPI()


@api.post("/item")
def item(request, item: Item) -> Item:
    return item


@api.post("/declared")
def declared(request, item: Item) -> Item | WrongType:
    return item


@api.post("/mixed")
def mixed(request, start: int = Body(2), end: int = Form(1)) -> list[int]:
    return [start, end]


@api.post("/form")
def form(request, end: int = Form(1)) -> int:
    return end


@api.post("/optional")
def optional(request, start: int = Body(2)) -> int:
    return start


urlpatterns = [path("api/", api.urls)]

client = TestClient(api)

JSON_TYPES = [
    "application/json",
    "application/json; charset=utf-8",
    "Application/JSON",
    "application/merge-patch+json",
    "application/vnd.api+json",
]

OTHER_TYPES = [
    # What a cross-site form or a no-preflight fetch can send.
    "text/plain",
    "text/plain;charset=UTF-8",
    "application/x-www-form-urlencoded",
    "multipart/form-data; boundary=x",
    # And what merely is not JSON.
    "application/octet-stream",
    "application/jsonx",
    "text/json",
]


@pytest.mark.parametrize("content_type", JSON_TYPES)
def test_body_sent_as_json_is_read(content_type):
    response = client.post("/item", body=b'{"a": 1}', content_type=content_type)
    assert response.status_code == 200
    assert response.json() == {"a": 1}


@pytest.mark.parametrize("content_type", OTHER_TYPES)
def test_body_sent_as_anything_else_is_refused(content_type):
    response = client.post("/item", body=b'{"a": 1}', content_type=content_type)
    assert response.status_code == 415
    assert response.json() == {
        "detail": "Unsupported Media Type: send the body as application/json"
    }


@pytest.mark.parametrize("content_type", ["", "application/", "+json", "json"])
def test_no_media_type_is_not_a_json_one(content_type):
    assert not is_json_media_type(content_type)


@pytest.mark.parametrize("content_type", ["", "; charset=utf-8"])
def test_body_sent_with_no_media_type_is_refused(settings, content_type):
    settings.ROOT_URLCONF = __name__

    response = Client().generic(
        "POST", "/api/item", data=b'{"a": 1}', content_type=content_type
    )

    assert response.status_code == 415


def test_request_with_no_body_still_reaches_an_optional_body():
    # Which is why the media type is no stand-in for a CSRF check.
    assert client.post("/optional", content_type="text/plain").json() == 2


def test_body_is_refused_before_it_is_read():
    response = client.post("/item", body=b"{not json", content_type="text/plain")
    assert response.status_code == 415


def test_malformed_json_sent_as_json_is_still_a_400():
    response = client.post("/item", body=b"{", content_type="application/json")
    assert response.status_code == 400


def test_request_without_a_body_is_not_held_to_a_media_type():
    response = client.post("/item")
    assert response.status_code == 422


def test_body_params_sent_as_form_fields_are_not_held_to_a_json_type():
    assert client.post("/mixed", POST={"start": "5", "end": "6"}).json() == [5, 6]
    assert client.post("/form", POST={"end": "6"}).json() == 6


def test_custom_error_model_shapes_the_415():
    class Problem(HttpErrorBody):
        status: int
        title: str

        @classmethod
        def from_error(cls, error):
            return cls(status=error.status_code, title=str(error))

    previous = get_http_error_model()
    set_http_error_model(Problem)
    try:
        response = client.post("/item", body=b'{"a": 1}', content_type="text/plain")
    finally:
        set_http_error_model(previous)

    assert response.status_code == 415
    assert response.json() == {
        "status": 415,
        "title": "Unsupported Media Type: send the body as application/json",
    }


def test_415_is_documented_where_a_json_body_is_read():
    paths = api.get_openapi_schema()["paths"]
    error = {"$ref": "#/components/schemas/HttpErrorResponse"}

    assert paths["/api/item"]["post"]["responses"][415] == {
        "description": "Unsupported Media Type",
        "content": {"application/json": {"schema": error}},
    }
    assert 415 not in paths["/api/mixed"]["post"]["responses"]
    assert 415 not in paths["/api/form"]["post"]["responses"]


def test_documented_415_keeps_a_declared_one_beside_it():
    responses = api.get_openapi_schema()["paths"]["/api/declared"]["post"]["responses"]

    assert responses[415]["content"]["application/json"]["schema"] == {
        "anyOf": [
            {"$ref": "#/components/schemas/WrongType"},
            {"$ref": "#/components/schemas/HttpErrorResponse"},
        ]
    }
