"""Comma-separated lists (``explode=False``) declared on fields of a query schema."""

from typing import Annotated

import pytest
from pydantic import Field

from hattori import HattoriAPI, Query, Schema
from hattori.testing import TestClient


class Inner(Schema):
    tag_ids: Annotated[list[str] | None, Query(explode=False, alias="tags")] = None


class Filters(Inner):
    account_ids: Annotated[
        list[str] | None, Query(explode=False, description="Accounts to include.")
    ] = None
    # Written the other way round, and combined with an ordinary Field.
    sizes: Annotated[list[int], Field(max_length=3)] = Query([], explode=False)
    names: list[str] | None = None
    search: str | None = None


class FiltersOut(Schema):
    account_ids: list[str] | None
    tag_ids: list[str] | None
    sizes: list[int]
    names: list[str] | None
    search: str | None


api = HattoriAPI()


@api.get("/items")
def list_items(request, filters: Query[Filters]) -> FiltersOut:
    return FiltersOut(**filters.model_dump())


client = TestClient(api)


@pytest.mark.parametrize(
    "query,expected",
    [
        ("account_ids=a,b", {"account_ids": ["a", "b"]}),
        # A repeated parameter is accepted too, and the two forms mix.
        ("account_ids=a&account_ids=b", {"account_ids": ["a", "b"]}),
        ("account_ids=a,b&account_ids=c", {"account_ids": ["a", "b", "c"]}),
        ("account_ids=a", {"account_ids": ["a"]}),
        ("account_ids=", {"account_ids": []}),
        ("tags=x,y", {"tag_ids": ["x", "y"]}),
        ("sizes=1,2,3", {"sizes": [1, 2, 3]}),
        # A list that isn't declared comma-separated keeps commas as data.
        ("names=a,b", {"names": ["a,b"]}),
        ("names=a&names=b", {"names": ["a", "b"]}),
        ("search=a,b", {"search": "a,b"}),
    ],
)
def test_schema_field_lists_split_only_where_declared(query, expected):
    response = client.get(f"/items?{query}")

    assert response.status_code == 200, response.content
    assert response.json() == {
        "account_ids": None,
        "tag_ids": None,
        "sizes": [],
        "names": None,
        "search": None,
        **expected,
    }


def test_split_values_are_still_validated():
    assert client.get("/items?sizes=1,2,3,4").status_code == 422
    assert client.get("/items?sizes=1,x").status_code == 422


def test_schema_field_lists_are_documented_as_comma_separated():
    parameters = {
        parameter["name"]: parameter
        for parameter in api.get_openapi_schema()["paths"]["/api/items"]["get"][
            "parameters"
        ]
    }

    for name in ("account_ids", "tags", "sizes"):
        assert (parameters[name]["style"], parameters[name]["explode"]) == (
            "form",
            False,
        )
    for name in ("names", "search"):
        assert "style" not in parameters[name]
        assert "explode" not in parameters[name]
    assert parameters["account_ids"]["description"] == "Accounts to include."
    # The marker that carries the declaration stays out of the schema itself.
    assert "explode" not in str(parameters["account_ids"]["schema"])
