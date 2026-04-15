from typing import Annotated

import pytest
from django.http import QueryDict  # noqa
from pydantic import BaseModel, ConfigDict, Field, conlist

from hattori import Body, Form, Query, Router, Schema
from hattori.testing import TestClient


class QueryFormResponse(Schema):
    query: list[int]
    form: list[int]


class QueryBodyResponse(Schema):
    query: list[int]
    body: list[int]


class BodyModel(BaseModel):
    x: int
    y: int


class BodyModelListResponse(Schema):
    body: list[BodyModel]


class BodyIntListResponse(Schema):
    body: list[int]


class QueryObjectIdResponse(Schema):
    query: list[int] | None = None


class CsvListResponse(Schema):
    ids: list[str]


class CsvListOptionalResponse(Schema):
    ids: list[str] | None = None


router = Router()


@router.post("/list1")
def listview1(
    request,
    query: list[int] = Query(...),
    form: list[int] = Form(...),
) -> QueryFormResponse:
    return {
            "query": query,
            "form": form,
        }


@router.post("/list2")
def listview2(
    request,
    body: list[int],
    query: list[int] = Query(...),
) -> QueryBodyResponse:
    return {
            "query": query,
            "body": body,
        }


@router.post("/list3")
def listview3(request, body: list[BodyModel]) -> BodyModelListResponse:
    return {
            "body": body,
        }


@router.post("/list-default")
def listviewdefault(request, body: list[int] = [1]) -> BodyIntListResponse:  # noqa: B006
    # By default List[anything] is treated for body
    return {
            "body": body,
        }


class Filters(Schema):
    model_config = ConfigDict(populate_by_name=True)
    tags: list[str] = []
    other_tags: list[str] = Field([], alias="other_tags_alias")


class FiltersResponse(Schema):
    filters: Filters


@router.post("/list4")
def listview4(
    request,
    filters: Filters = Query(...),
) -> FiltersResponse:
    return {
            "filters": filters,
        }


class ConListSchema(Schema):
    query: conlist(int, min_length=1)


class Data(Schema):
    data: ConListSchema


@router.post("/list5")
def listview5(
    request,
    body: conlist(int, min_length=1) = Body(...),
    a_query: Data = Query(...),
) -> QueryBodyResponse:
    return {
            "query": a_query.data.query,
            "body": body,
        }


@router.post("/list6")
def listview6(
    request,
    object_id: list[int] = Query(None, alias="id"),
) -> QueryObjectIdResponse:
    return {"query": object_id}


@router.get("/list-csv")
def list_csv(
    request,
    ids: list[str] = Query(..., explode=False),
) -> CsvListResponse:
    return {"ids": ids}


@router.get("/list-csv-optional")
def list_csv_optional(
    request,
    ids: Annotated[list[str] | None, Query(explode=False)] = None,  # pyright: ignore[reportCallIssue]
) -> CsvListOptionalResponse:
    return {"ids": ids}


client = TestClient(router)


@pytest.mark.parametrize(
    # fmt: off
    "path,kwargs,expected_response",
    [
        (
            "/list1?query=1&query=2",
            dict(data=QueryDict("form=3&form=4")),
            {"query": [1, 2], "form": [3, 4]},
        ),
        (
            "/list2?query=1&query=2",
            dict(json=[5, 6]),
            {"query": [1, 2], "body": [5, 6]},
        ),
        (
            "/list3",
            dict(json=[{"x": 1, "y": 1}]),
            {"body": [{"x": 1, "y": 1}]},
        ),
        (
            "/list-default",
            {},
            {"body": [1]},
        ),
        (
            "/list-default",
            dict(json=[1, 2]),
            {"body": [1, 2]},
        ),
        (
            "/list4?tags=a&tags=b&other_tags_alias=a&other_tags_alias=b",
            {},
            {"filters": {"tags": ["a", "b"], "other_tags": ["a", "b"]}},
        ),
        (
            "/list4?tags=abc&other_tags_alias=abc",
            {},
            {"filters": {"tags": ["abc"], "other_tags": ["abc"]}},
        ),
        (
            "/list4",
            {},
            {"filters": {"tags": [], "other_tags": []}},
        ),
        (
            "/list5?query=1&query=2",
            dict(json=[5, 6]),
            {"query": [1, 2], "body": [5, 6]},
        ),
        (
            "/list6?id=1&id=2",
            {},
            {"query": [1, 2]},
        ),
    ],
    # fmt: on
)
def test_list(path, kwargs, expected_response):
    response = client.post(path, **kwargs)
    assert response.status_code == 200, response.content
    assert response.json() == expected_response


@pytest.mark.parametrize(
    "path,expected_response",
    [
        # single csv value
        ("/list-csv?ids=a,b,c", {"ids": ["a", "b", "c"]}),
        # mixed: csv + repeated
        ("/list-csv?ids=a,b&ids=c", {"ids": ["a", "b", "c"]}),
        # single value, no commas
        ("/list-csv?ids=a", {"ids": ["a"]}),
        # empty segments filtered out
        ("/list-csv?ids=a,,b", {"ids": ["a", "b"]}),
        # optional with csv
        ("/list-csv-optional?ids=a,b", {"ids": ["a", "b"]}),
        # optional with no param
        ("/list-csv-optional", {"ids": None}),
    ],
)
def test_csv_query(path, expected_response):
    response = client.get(path)
    assert response.status_code == 200, response.content
    assert response.json() == expected_response


def _get_schema():
    from hattori import HattoriAPI

    api = HattoriAPI()
    api.add_router("", router)
    return api.get_openapi_schema()


def test_csv_query_openapi_schema():
    schema = _get_schema()
    params = schema["paths"]["/api/list-csv"]["get"]["parameters"]
    ids_param = next(p for p in params if p["name"] == "ids")
    assert ids_param["style"] == "form"
    assert ids_param["explode"] is False


def test_non_csv_query_has_no_explode():
    schema = _get_schema()
    params = schema["paths"]["/api/list6"]["post"]["parameters"]
    id_param = next(p for p in params if p["name"] == "id")
    assert "style" not in id_param
    assert "explode" not in id_param
