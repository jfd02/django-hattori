from typing import Annotated

import pytest

from hattori import Field, HattoriAPI, Schema
from hattori.patch_dict import PatchDict, PatchName, create_patch_schema
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


class NullableNoDefaultSchema(Schema):
    name: str
    # nullable but with no default -> still a *required* field in pydantic v2
    note: str | None


nullable_api = HattoriAPI()
nullable_client = TestClient(nullable_api)


@nullable_api.patch("/patch-nullable")
def patch_nullable(
    request, payload: PatchDict[NullableNoDefaultSchema]
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
def patch_inherited(request, payload: PatchDict[OtherSchema]) -> PatchPayloadTypeResult:
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


def test_patch_nullable_without_default_is_optional():
    """A nullable field with no default must still be optional in PatchDict."""
    # Omitting the nullable-without-default field must not be a validation error.
    response = nullable_client.patch("/patch-nullable", json={})
    assert response.status_code == 200
    assert response.json() == {"payload": {}}

    # Explicitly setting it to null still works.
    response = nullable_client.patch("/patch-nullable", json={"note": None})
    assert response.status_code == 200
    assert response.json() == {"payload": {"note": None}}

    # And setting a value works too.
    response = nullable_client.patch("/patch-nullable", json={"note": "hi"})
    assert response.status_code == 200
    assert response.json() == {"payload": {"note": "hi"}}


def test_patch_constrained_partial_update():
    """PatchDict with constraints should still allow partial updates."""
    response = constrained_client.patch("/patch-constrained", json={"name": "hi"})
    assert response.status_code == 200
    assert response.json() == {"payload": {"name": "hi"}}

    response = constrained_client.patch("/patch-constrained", json={})
    assert response.status_code == 200
    assert response.json() == {"payload": {}}


# --- Naming the generated patch schema independently of the source class ---


class FlagsSchema(Schema):
    dark_mode: bool
    beta_features: bool


named_api = HattoriAPI()
named_client = TestClient(named_api)


@named_api.patch("/patch-default-name")
def patch_default_name(request, payload: PatchDict[FlagsSchema]) -> PatchPayloadResult:
    return {"payload": payload}


@named_api.patch("/patch-custom-name")
def patch_custom_name(
    request, payload: PatchDict[Annotated[FlagsSchema, PatchName("UserFlags")]]
) -> PatchPayloadResult:
    return {"payload": payload}


def test_patch_schema_name_defaults_to_source_class():
    schema = named_api.get_openapi_schema()
    body = schema["paths"]["/api/patch-default-name"]["patch"]["requestBody"]
    ref = body["content"]["application/json"]["schema"]["$ref"]
    assert ref.rsplit("/", 1)[-1] == "FlagsSchemaPatch"


def test_patch_name_overrides_generated_schema_name():
    """The client-facing name is decoupled from the internal class name, and is
    used verbatim — no "Patch" suffix is appended."""
    schema = named_api.get_openapi_schema()
    body = schema["paths"]["/api/patch-custom-name"]["patch"]["requestBody"]
    ref = body["content"]["application/json"]["schema"]["$ref"]
    assert ref.rsplit("/", 1)[-1] == "UserFlags"
    assert schema["components"]["schemas"]["UserFlags"]["title"] == "UserFlags"


def test_patch_name_does_not_change_patch_semantics():
    response = named_client.patch("/patch-custom-name", json={"beta_features": True})
    assert response.status_code == 200
    assert response.json() == {"payload": {"beta_features": True}}

    response = named_client.patch("/patch-custom-name", json={})
    assert response.status_code == 200
    assert response.json() == {"payload": {}}


def test_patch_name_leaves_the_source_schema_alone():
    assert FlagsSchema.__name__ == "FlagsSchema"


def test_create_patch_schema_accepts_a_name_directly():
    assert create_patch_schema(FlagsSchema)._wrapped_model.__name__ == (
        "FlagsSchemaPatch"
    )
    assert create_patch_schema(
        FlagsSchema, name="Explicit"
    )._wrapped_model.__name__ == ("Explicit")
