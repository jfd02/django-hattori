from typing import Annotated

from pydantic import Field

from hattori import Body, HattoriAPI, Header, Query
from hattori.testing import TestClient

api = HattoriAPI()


@api.get("/query-list")
def query_list(
    request, ids: Annotated[Query[list[int]], Field(min_length=1)]
) -> list[int]:
    return ids


@api.get("/query-default")
def query_default(request, n: Annotated[Query[int], Field(ge=1)] = 5) -> int:
    return n


@api.get("/query-inner-default")
def query_inner_default(request, n: Query[Annotated[int, Field(ge=1)]] = 5) -> int:
    return n


@api.get("/header")
def header(request, token: Annotated[Header[str], Field(min_length=2)]) -> str:
    return token


@api.post("/body")
def body(request, n: Annotated[Body[int], Field(ge=1)]) -> int:
    return n


client = TestClient(api)


def operation(path, method="get"):
    return api.get_openapi_schema()["paths"][path][method]


def test_query_param_with_more_metadata_is_read_from_the_query():
    assert client.get("/query-list?ids=1&ids=2").json() == [1, 2]
    response = client.get("/query-list")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "ids"]


def test_query_param_with_more_metadata_keeps_its_constraints():
    response = client.get("/query-default?n=0")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "n"]
    assert client.get("/query-default").json() == 5
    assert client.get("/query-default?n=2").json() == 2


def test_default_of_a_param_with_more_metadata_is_kept_either_way_round():
    assert client.get("/query-inner-default").json() == 5
    assert client.get("/query-inner-default?n=0").status_code == 422
    [n] = operation("/api/query-inner-default")["parameters"]
    assert (n["required"], n["schema"]["default"]) == (False, 5)


def test_header_param_with_more_metadata_is_read_from_the_headers():
    assert client.get("/header", headers={"token": "abc"}).json() == "abc"
    response = client.get("/header", headers={"token": "a"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["header", "token"]
    assert client.get("/header?token=abc").status_code == 422


def test_body_param_with_more_metadata_is_read_from_the_body():
    response = client.post("/body", data="3", content_type="application/json")
    assert response.json() == 3
    response = client.post("/body", data="0", content_type="application/json")
    assert response.status_code == 422


def test_params_with_more_metadata_are_documented_where_they_are_read():
    [ids] = operation("/api/query-list")["parameters"]
    assert ids["in"] == "query"
    assert ids["name"] == "ids"
    assert ids["required"] is True
    assert ids["schema"]["minItems"] == 1
    assert "requestBody" not in operation("/api/query-list")

    [n] = operation("/api/query-default")["parameters"]
    assert (n["in"], n["required"]) == ("query", False)
    assert n["schema"]["minimum"] == 1
    assert n["schema"]["default"] == 5

    [token] = operation("/api/header")["parameters"]
    assert (token["in"], token["required"]) == ("header", True)
    assert token["schema"]["minLength"] == 2
