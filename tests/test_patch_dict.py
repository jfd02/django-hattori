
import pytest

from hattori import Field, HattoriAPI, Schema
from hattori.patch_dict import PatchDict
from hattori.testing import TestClient


class PatchPayloadResult(Schema):
    payload: dict


class PatchPayloadTypeResult(Schema):
    payload: dict
    type: str


api = HattoriAPI()

client = TestClient(api)


# -- Schema with Field constraints for testing preservation --


class ConstrainedSchema(Schema):
    name: str = Field(max_length=5)
    price: int = Field(ge=0)
    tag: str | None = None


constrained_api = HattoriAPI()
constrained_client = TestClient(constrained_api)


@constrained_api.patch("/patch-constrained")
def patch_constrained(
    request, payload: PatchDict[ConstrainedSchema]
) -> PatchPayloadResult:
    return {"payload": payload}


class SomeSchema(Schema):
    name: str
    age: int
    category: str | None = None


class OtherSchema(SomeSchema):
    other: str
    category: list[str] | None = None


@api.patch("/patch")
def patch(request, payload: PatchDict[SomeSchema]) -> PatchPayloadTypeResult:
    return {"payload": payload, "type": str(type(payload))}


@api.patch("/patch-inherited")
def patch_inherited(
    request, payload: PatchDict[OtherSchema]
) -> PatchPayloadTypeResult:
    return {"payload": payload, "type": str(type(payload))}


@pytest.mark.parametrize(
    "input,output",
    [
        ({"name": "foo"}, {"name": "foo"}),
        ({"age": "1"}, {"age": 1}),
        ({}, {}),
        ({"wrong_param": 1}, {}),
        ({"age": None}, {"age": None}),
    ],
)
def test_patch_calls(input: dict, output: dict):
    response = client.patch("/patch", json=input)
    assert response.json() == {"payload": output, "type": "<class 'dict'>"}


def test_schema():
    "Checking that json schema properties are all optional"
    schema = api.get_openapi_schema()
    assert schema["components"]["schemas"]["SomeSchemaPatch"] == {
        "title": "SomeSchemaPatch",
        "type": "object",
        "properties": {
            "name": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "title": "Name",
            },
            "age": {
                "anyOf": [{"type": "integer"}, {"type": "null"}],
                "title": "Age",
            },
            "category": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "title": "Category",
            },
        },
    }


def test_patch_inherited():
    input = {"other": "any", "category": ["cat1", "cat2"]}
    expected_output = {"payload": input, "type": "<class 'dict'>"}

    response = client.patch("/patch-inherited", json=input)
    assert response.json() == expected_output


def test_inherited_schema():
    "Checking that json schema properties for inherithed schemas are ok"
    schema = api.get_openapi_schema()
    assert schema["components"]["schemas"]["OtherSchemaPatch"] == {
        "title": "OtherSchemaPatch",
        "type": "object",
        "properties": {
            "name": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "title": "Name",
            },
            "age": {
                "anyOf": [{"type": "integer"}, {"type": "null"}],
                "title": "Age",
            },
            "other": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "title": "Other",
            },
            "category": {
                "anyOf": [
                    {
                        "items": {
                            "type": "string",
                        },
                        "type": "array",
                    },
                    {"type": "null"},
                ],
                "title": "Category",
            },
        },
    }


def test_patch_preserves_max_length():
    """PatchDict should enforce max_length from Field constraints."""
    response = constrained_client.patch("/patch-constrained", json={"name": "ok"})
    assert response.status_code == 200

    response = constrained_client.patch(
        "/patch-constrained", json={"name": "way too long"}
    )
    assert response.status_code == 422


def test_patch_preserves_ge():
    """PatchDict should enforce ge=0 from Field constraints."""
    response = constrained_client.patch("/patch-constrained", json={"price": 10})
    assert response.status_code == 200

    response = constrained_client.patch("/patch-constrained", json={"price": -1})
    assert response.status_code == 422


def test_patch_constrained_partial_update():
    """PatchDict with constraints should still allow partial updates."""
    response = constrained_client.patch("/patch-constrained", json={"name": "hi"})
    assert response.status_code == 200
    assert response.json() == {"payload": {"name": "hi"}}

    response = constrained_client.patch("/patch-constrained", json={})
    assert response.status_code == 200
    assert response.json() == {"payload": {}}
