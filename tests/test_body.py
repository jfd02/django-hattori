from typing import Any

import pytest
from pydantic import field_validator

from hattori import Body, Form, HattoriAPI, Schema
from hattori.errors import ConfigError, ValidationError, ValidationErrorContext
from hattori.testing import TestClient

api = HattoriAPI()

# testing Body marker:


@api.post("/task")
def create_task(
    request, start: int = Body(...), end: int = Body(...)
) -> list[int]:
    return [start, end]


@api.post("/task2")
def create_task2(
    request, start: int = Body(2), end: int = Form(1)
) -> list[int]:
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
    return payload.dict()


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
            "loc": ["body", "payload", "email"],
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
    return payload.dict()


custom_error_client = TestClient(custom_error_api)


def test_body_custom_validation_error():
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
