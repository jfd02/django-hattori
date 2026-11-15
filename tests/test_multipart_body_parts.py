"""A Body param sent beside File/Form params is a part of the multipart body.

It is read the way OpenAPI's default multipart encoding reads the property the
spec documents it as: a primitive is its plain text value, anything else JSON.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal, TypedDict

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from pydantic import Field, HttpUrl, Json, RootModel
from pydantic_core import core_schema, to_jsonable_python

from hattori import Body, File, HattoriAPI, Schema, UploadedFile
from hattori.testing import TestClient


class Color(str, Enum):
    red = "red"


class Item(Schema):
    name: str = "default"


class Point(TypedDict):
    x: int


@dataclass
class DataPoint:
    x: int


class Name(RootModel[str]):
    pass


class Opaque:
    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        return core_schema.no_info_plain_validator_function(lambda v: v)


type Nickname = str


def post_part(annotation: Any, value: str):
    api = HattoriAPI()

    def view(request, file: File[UploadedFile], data=Body(...)) -> Any:
        return {"data": to_jsonable_python(data)}

    view.__annotations__["data"] = annotation
    api.post("/part")(view)

    file = SimpleUploadedFile("test.txt", b"data123")
    return TestClient(api).post("/part", FILES={"file": file}, POST={"data": value})


@pytest.mark.parametrize(
    "annotation,value,expected",
    [
        (int, "42", 42),
        (date, "2024-01-02", "2024-01-02"),
        (Color, "red", "red"),
        (Color | None, "red", "red"),
        (str | None, "hello", "hello"),
        (Annotated[str, Field(min_length=1)], '{"a": 1}', '{"a": 1}'),
        (Literal[1, "a"], "a", "a"),
        (Decimal, "1.50", "1.50"),
        (HttpUrl, "https://example.com/", "https://example.com/"),
        (Name, "bob", "bob"),
        (Nickname, "bob", "bob"),
        (Json[dict[str, int]], '{"a": 1}', {"a": 1}),
    ],
)
def test_primitive_part_is_read_as_plain_text(annotation, value, expected):
    response = post_part(annotation, value)
    assert response.status_code == 200, response.content
    assert response.json() == {"data": expected}


@pytest.mark.parametrize(
    "annotation,value,expected",
    [
        (dict, '{"a": 1}', {"a": 1}),
        (dict[str, int] | None, '{"a": 1}', {"a": 1}),
        (Point, '{"x": 1}', {"x": 1}),
        (DataPoint, '{"x": 1}', {"x": 1}),
        (Annotated[list[int], Field(min_length=1)], "[1, 2]", [1, 2]),
        (Any, '{"a": 1}', {"a": 1}),
        (Item | None, '{"name": "sent"}', {"name": "sent"}),
        (Literal["none"] | Item, '{"name": "sent"}', {"name": "sent"}),
        (Opaque, '{"a": 1}', {"a": 1}),
        (str | dict[str, int], '{"a": 1}', {"a": 1}),
        (str | dict[str, int], '"hello"', "hello"),
    ],
)
def test_other_part_is_read_as_json(annotation, value, expected):
    response = post_part(annotation, value)
    assert response.status_code == 200, response.content
    assert response.json() == {"data": expected}


def test_empty_json_part_is_none():
    assert post_part(dict | None, "").json() == {"data": None}


def test_empty_primitive_part_is_validated_like_a_form_field():
    assert post_part(str, "").json() == {"data": ""}
    response = post_part(int | None, "")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "data"]


def test_schema_list_and_dict_parts_together():
    api = HattoriAPI()

    class Details(Schema):
        title: str

    @api.post("/json-parts")
    def json_parts(
        request,
        details: Details,
        tags: list[str] = Body(...),
        meta: dict[str, int] = Body(...),
        when: date = Body(...),
        file: UploadedFile = File(...),
    ) -> Any:
        return {"title": details.title, "tags": tags, "meta": meta, "when": when}

    file = SimpleUploadedFile("test.txt", b"data123")
    response = TestClient(api).post(
        "/json-parts",
        FILES={"file": file},
        POST={
            "details": '{"title": "say \\"hi\\""}',
            "tags": '["a", "b"]',
            "meta": '{"x": 1}',
            "when": "2024-01-02",
        },
    )
    assert response.status_code == 200, response.content
    assert response.json() == {
        "title": 'say "hi"',
        "tags": ["a", "b"],
        "meta": {"x": 1},
        "when": "2024-01-02",
    }
