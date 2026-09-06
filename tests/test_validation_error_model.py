"""The 422 body is declared once and drives both the response and the spec.

Before ``set_validation_error_model`` the only way to change the shape was to
override a private schema helper *and* register a ``ValidationError`` handler,
leaving two declarations to keep in step by hand — and a spec that lied about
the response whenever they drifted.
"""

from typing import Any, Literal, Self

import pytest
from pydantic import BaseModel, field_validator

from hattori import HattoriAPI, Query, Schema, ValidationErrorBody
from hattori.errors import (
    ConfigError,
    ValidationErrorResponse,
    get_validation_error_model,
    set_validation_error_model,
)
from hattori.testing import TestClient


class Out(Schema):
    ok: int


class EmailIn(Schema):
    address: str

    @field_validator("address")
    @classmethod
    def check(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError("invalid email")
        return value


class FieldProblem(BaseModel):
    path: list[str | int]
    reason: str


class Problem(ValidationErrorBody):
    """RFC7807-ish 422 body: nothing like the shipped ``{detail: [...]}``."""

    code: Literal["validation_error"] = "validation_error"
    problems: list[FieldProblem]

    @classmethod
    def from_errors(cls, errors: list[dict[str, Any]]) -> Self:
        return cls(
            problems=[
                FieldProblem(path=list(e["loc"]), reason=e["msg"]) for e in errors
            ]
        )


api = HattoriAPI()


@api.get("/items")
def get_items(request, count: int = Query(...)) -> Out:
    return Out(ok=count)


@api.post("/emails")
def post_email(request, payload: EmailIn) -> Out:
    return Out(ok=1)


client = TestClient(api)


@pytest.fixture
def problem_model():
    original = get_validation_error_model()
    set_validation_error_model(Problem)
    try:
        yield
    finally:
        set_validation_error_model(original)


def _documented_422(schema):
    ref = schema["paths"]["/api/items"]["get"]["responses"][422]["content"][
        "application/json"
    ]["schema"]["$ref"]
    return schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]


# --- Default ---


def test_default_model_is_the_shipped_response():
    assert get_validation_error_model() is ValidationErrorResponse


def test_default_response_and_schema_agree():
    r = client.get("/items?count=abc")
    assert r.status_code == 422
    documented = _documented_422(api.get_openapi_schema())
    assert set(r.json()) == set(documented["properties"]) == {"detail"}


def test_default_detail_entries_document_the_ctx_they_carry():
    """pydantic attaches ``ctx`` to some errors, so the documented entry has to
    admit properties beyond loc/msg/type."""
    schema = api.get_openapi_schema()
    entry_ref = _documented_422(schema)["properties"]["detail"]["items"]["$ref"]
    entry = schema["components"]["schemas"][entry_ref.rsplit("/", 1)[-1]]
    assert entry["additionalProperties"] is True
    assert set(entry["required"]) == {"loc", "msg", "type"}


def test_ctx_still_reaches_the_client():
    """The extra pydantic attaches to a validator rejection survives being
    carried through the model — that is what ``additionalProperties`` documents."""
    r = client.post("/emails", json={"address": "nope"})
    assert r.status_code == 422
    assert r.json()["detail"] == [
        {
            "loc": ["body", "address"],
            "msg": "Value error, invalid email",
            "type": "value_error",
            "ctx": {"error": "invalid email"},
        }
    ]


def test_loc_is_request_relative():
    r = client.get("/items?count=abc")
    assert r.json()["detail"][0]["loc"] == ["query", "count"]


# --- Custom ---


def test_custom_model_changes_the_response(problem_model):
    r = client.get("/items?count=abc")
    assert r.status_code == 422
    assert r.json() == {
        "code": "validation_error",
        "problems": [
            {
                "path": ["query", "count"],
                "reason": "Input should be a valid integer, unable to parse "
                "string as an integer",
            }
        ],
    }


def test_custom_model_changes_the_schema_to_match(problem_model):
    documented = _documented_422(api.get_openapi_schema())
    assert set(documented["properties"]) == {"code", "problems"}


def test_response_and_schema_cannot_drift(problem_model):
    """The single declaration is what makes this hold for any model."""
    r = client.get("/items?count=abc")
    documented = _documented_422(api.get_openapi_schema())
    assert set(r.json()) == set(documented["properties"])


def test_swapping_back_restores_the_default(problem_model):
    set_validation_error_model(ValidationErrorResponse)
    assert set(client.get("/items?count=abc").json()) == {"detail"}
    assert set(_documented_422(api.get_openapi_schema())["properties"]) == {"detail"}


# --- Guardrails ---


def test_model_must_subclass_the_base():
    class NotABody(BaseModel):
        detail: str

    with pytest.raises(ConfigError):
        set_validation_error_model(NotABody)  # type: ignore[arg-type]
    assert get_validation_error_model() is ValidationErrorResponse


def test_base_from_errors_is_not_implemented():
    class Incomplete(ValidationErrorBody):
        pass

    with pytest.raises(NotImplementedError):
        Incomplete.from_errors([])
