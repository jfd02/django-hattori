from decimal import Decimal
from typing import Any

import pydantic
import pytest
from pydantic import field_validator

from hattori import Body, Form, HattoriAPI, Schema
from hattori.errors import (
    ConfigError,
    ValidationError,
    ValidationErrorBody,
    ValidationErrorContext,
    get_validation_error_model,
    set_validation_error_model,
)
from hattori.testing import TestClient

api = HattoriAPI()

# testing Body marker:


@api.post("/task")
def create_task(request, start: int = Body(...), end: int = Body(...)) -> list[int]:
    return [start, end]


@api.post("/task2")
def create_task2(request, start: int = Body(2), end: int = Form(1)) -> list[int]:
    return [start, end]


class UserIn(Schema):
    # for testing validation errors context
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if "@" not in v:
            raise ValueError("invalid email")
        return v


@api.post("/users")
def create_user(request, payload: UserIn) -> UserIn:
    return payload.model_dump()


client = TestClient(api)


def test_body():
    assert client.post("/task", json={"start": 1, "end": 2}).json() == [1, 2]
    assert client.post("/task", json={"start": 1}).json() == {
        "detail": [{"type": "missing", "loc": ["body", "end"], "msg": "Field required"}]
    }


def test_body_form():
    data = client.post("/task2", POST={"start": "1", "end": "2"}).json()
    print(data)
    assert client.post("/task2", POST={"start": "1", "end": "2"}).json() == [1, 2]
    assert client.post("/task2").json() == [2, 1]


def test_body_validation_error():
    resp = client.post("/users", json={"email": "valid@email.com"})
    assert resp.status_code == 200

    resp = client.post("/users", json={"email": "invalid.com"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == [
        {
            "type": "value_error",
            "loc": ["body", "email"],
            "msg": "Value error, invalid email",
            "ctx": {"error": "invalid email"},
        }
    ]


def test_incorrect_annotation():
    api = HattoriAPI()

    class Some(Schema):
        a: int

    with pytest.raises(ConfigError):

        @api.post("/some")
        def some(request, payload=Some) -> int:
            #  ................. ^------ invalid usage assigning class instead of annotation
            return 42


class CustomErrorAPI(HattoriAPI):
    def validation_error_from_error_contexts(
        self,
        error_contexts: list[ValidationErrorContext],
    ) -> ValidationError:
        errors: list[dict[str, Any]] = []
        for context in error_contexts:
            model = context.model
            for e in context.pydantic_validation_error.errors(
                include_url=False, include_context=False, include_input=False
            ):
                errors.append({
                    "source": model.__hattori_param_source__,
                    "message": e["msg"],
                })
        return ValidationError(errors)


custom_error_api = CustomErrorAPI()


@custom_error_api.post("/users")
def create_user2(request, payload: UserIn) -> UserIn:
    return payload.model_dump()


custom_error_client = TestClient(custom_error_api)


class SourceMessage(pydantic.BaseModel):
    source: str
    message: str


class SourceMessageErrors(ValidationErrorBody):
    """The 422 body matching CustomErrorAPI's reshaped error entries."""

    detail: list[SourceMessage]

    @classmethod
    def from_errors(cls, errors: list[dict[str, Any]]):
        return cls.model_validate({"detail": errors})


def test_body_custom_validation_error():
    """Reshaping the error entries requires declaring the body that carries
    them, and that one declaration drives both the response and the spec."""
    original = get_validation_error_model()
    set_validation_error_model(SourceMessageErrors)
    try:
        resp = custom_error_client.post("/users", json={"email": "valid@email.com"})
        assert resp.status_code == 200

        resp = custom_error_client.post("/users", json={"email": "invalid.com"})
        assert resp.status_code == 422
        assert resp.json()["detail"] == [
            {
                "source": "body",
                "message": "Value error, invalid email",
            }
        ]

        schema = custom_error_api.get_openapi_schema()
        ref = schema["paths"]["/api/users"]["post"]["responses"][422]["content"][
            "application/json"
        ]["schema"]["$ref"]
        documented = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]
        entry_ref = documented["properties"]["detail"]["items"]["$ref"]
        entry = schema["components"]["schemas"][entry_ref.rsplit("/", 1)[-1]]
        assert set(entry["properties"]) == {"source", "message"}
    finally:
        set_validation_error_model(original)


# An integer beyond 64 bits arrives whole, whatever it is read into.

large_api = HattoriAPI()


class Count(Schema):
    n: int


@large_api.post("/decimal")
def large_decimal(request, value: Body[Decimal]) -> str:
    return str(value)


@large_api.post("/int")
def large_int(request, value: Body[int]) -> str:
    return str(value)


@large_api.post("/schema")
def large_schema(request, payload: list[Count]) -> list[str]:
    return [str(count.n) for count in payload]


large_client = TestClient(large_api)


@pytest.mark.parametrize(
    "value", [2**64 + 1, -(2**63) - 1, 2**64 - 1, -(2**63), 10**100]
)
@pytest.mark.parametrize("path", ["/decimal", "/int"])
def test_large_integer_body(path, value):
    response = large_client.post(path, json=value)
    assert response.status_code == 200, response.content
    assert response.json() == str(value)


def test_large_integers_inside_a_body():
    body = '[{"n": 1}, {"n": 18446744073709551617}, {"n": -12345678901234567890}]'
    response = large_client.post("/schema", data=body, content_type="application/json")
    assert response.status_code == 200, response.content
    assert response.json() == ["1", "18446744073709551617", "-12345678901234567890"]


@pytest.mark.parametrize(
    "body",
    [
        "[18446744073709551617, NaN]",
        "[18446744073709551617, Infinity]",
        "[18446744073709551617,]",
        '["\\ud800", 18446744073709551617]',
    ],
)
def test_body_with_large_integer_is_still_strict_json(body):
    response = large_client.post("/schema", data=body, content_type="application/json")
    assert response.status_code == 400, response.content


@pytest.mark.parametrize(
    "text,value",
    [
        ("0.0012345678901234567891", 0.0012345678901234567891),
        ('{"id": "12345678901234567890"}', {"id": "12345678901234567890"}),
        ("[1e5, 1844674407370955161]", [1e5, 1844674407370955161]),
    ],
)
def test_json_loads_reads_long_digit_runs_as_written(text, value):
    from hattori.responses import json_loads

    assert json_loads(text) == value
    assert json_loads(text.encode()) == value


@pytest.mark.xfail(
    strict=True,
    reason=(
        "A JSON number is read as a float before it is validated, so a Decimal "
        "gets only the digits a float holds. To be handled."
    ),
)
@pytest.mark.parametrize(
    "value", ["1.0000000000000000000001", "3.14159265358979323846264338"]
)
def test_decimal_body_keeps_every_digit(value):
    response = large_client.post(
        "/decimal", data=value, content_type="application/json"
    )
    assert response.status_code == 200, response.content
    assert response.json() == value
