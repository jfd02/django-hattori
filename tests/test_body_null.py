from typing import Annotated

import pytest
from pydantic import AfterValidator, ValidationInfo

from hattori import Body, HattoriAPI
from hattori.testing import TestAsyncClient, TestClient

api = HattoriAPI()


@api.post("/two")
def two(request, a: int = Body(1), b: int = Body(2)) -> dict:
    return {"a": a, "b": b}


@api.post("/two-required")
def two_required(request, a: int = Body(...), b: int = Body(...)) -> dict:
    return {"a": a, "b": b}


@api.post("/two-async")
async def two_async(request, a: int = Body(1), b: int = Body(2)) -> dict:
    return {"a": a, "b": b}


@api.post("/one")
def one(request, a: int = Body(1)) -> dict:
    return {"a": a}


@api.post("/one-nullable")
def one_nullable(request, a: int | None = Body(1)) -> dict:
    return {"a": a}


client = TestClient(api)


def post_json(path, body):
    return client.post(path, data=body, content_type="application/json")


def test_null_body_of_several_params_is_refused():
    response = post_json("/two", "null")
    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["loc"] == ["body"]
    assert error["type"] == "model_type"


@pytest.mark.asyncio
async def test_null_body_of_several_params_is_refused_by_an_async_view():
    response = await TestAsyncClient(api).post(
        "/two-async", data="null", content_type="application/json"
    )
    assert response.status_code == 422


def test_null_body_of_several_required_params_is_refused():
    assert post_json("/two-required", "null").status_code == 422


def test_missing_body_of_several_params_still_takes_their_defaults():
    assert client.post("/two").json() == {"a": 1, "b": 2}
    response = client.post("/two-required")
    assert response.status_code == 422
    assert [e["loc"] for e in response.json()["detail"]] == [
        ["body", "a"],
        ["body", "b"],
    ]


def test_body_of_several_params_is_still_read():
    assert post_json("/two", '{"a": 5}').json() == {"a": 5, "b": 2}


def test_null_body_of_one_param_is_still_that_param_null():
    assert post_json("/one", "null").status_code == 422
    assert post_json("/one-nullable", "null").json() == {"a": None}
    assert client.post("/one").json() == {"a": 1}


def test_params_validated_without_a_body_see_the_request():
    seen = []

    def remember(value: int, info: ValidationInfo) -> int:
        seen.append(info.context)
        return value

    temp_api = HattoriAPI()

    @temp_api.post("/two")
    def two(
        request,
        a: Annotated[int, AfterValidator(remember)] = Body(1, validate_default=True),
        b: int = Body(2),
    ) -> dict:
        return {"a": a, "b": b}

    response = TestClient(temp_api).post("/two")
    assert response.json() == {"a": 1, "b": 2}
    [context] = seen
    assert context is not None
    assert "request" in context
