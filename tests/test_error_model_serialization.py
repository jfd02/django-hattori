"""The framework's error bodies are documented the way they are sent.

The ``HttpError`` and 422 models are handed to the renderer, which dumps them
as ``model_dump()`` does when given no arguments. Their schemas used to be
generated for validation and by alias instead, so a serializer, a computed
field, an alias or an excluded field made the spec describe a body the API
never sent.
"""

import dataclasses
from typing import Annotated, Any, Self, TypedDict

import pytest
from openapi_contract import export_contract, validate_response
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer
from pydantic.dataclasses import dataclass
from pydantic_core import core_schema

from hattori import HattoriAPI, HttpErrorBody, Schema
from hattori.errors import (
    HttpError,
    ValidationErrorBody,
    get_http_error_model,
    get_validation_error_model,
    set_http_error_model,
    set_validation_error_model,
)
from hattori.openapi.schema import DumpedModelJsonSchema
from hattori.testing import TestClient

MESSAGE = "Cannot parse request body"


class Item(Schema):
    name: str


class Serialized(HttpErrorBody):
    status: int

    @field_serializer("status")
    def _as_text(self, value: int) -> str:
        return str(value)

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        return cls(status=error.status_code)


class SerializedForJsonOnly(HttpErrorBody):
    """The body is dumped in Python mode, where this serializer does not run."""

    status: int

    @field_serializer("status", when_used="json")
    def _as_text(self, value: int) -> str:
        return str(value)

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        return cls(status=error.status_code)


class _BuiltinSerializer:
    """An ``int`` with one of pydantic's own serializers, which are for JSON."""

    def __init__(self, serialization):
        self.serialization = serialization

    def __get_pydantic_core_schema__(self, source, handler):
        return core_schema.int_schema(serialization=self.serialization)


class SerializedByBuiltins(HttpErrorBody):
    """Neither serializer says when it is used, and neither runs in Python mode."""

    status: Annotated[int, _BuiltinSerializer(core_schema.to_string_ser_schema())]
    padded: Annotated[int, _BuiltinSerializer(core_schema.format_ser_schema("04d"))]

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        return cls(status=error.status_code, padded=error.status_code)


class Computed(HttpErrorBody):
    detail: str

    @computed_field
    @property
    def kind(self) -> str:
        return "http_error"

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        return cls(detail=str(error))


class Aliased(HttpErrorBody):
    error_message: str = Field(alias="errorMessage")

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        return cls(errorMessage=str(error))


class SerializedByAlias(HttpErrorBody):
    model_config = ConfigDict(serialize_by_alias=True)

    error_message: str = Field(serialization_alias="errorMessage")

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        return cls(error_message=str(error))


class Excluded(HttpErrorBody):
    detail: str
    internal: str = Field(exclude=True)

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        return cls(detail=str(error), internal="not for the client")


class AliasedInner(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    error_message: str = Field(serialization_alias="errorMessage")


class PlainInner(BaseModel):
    error_message: str = Field(serialization_alias="errorMessage")


@dataclass(config=ConfigDict(serialize_by_alias=True))
class AliasedDataclass:
    error_message: str = Field(serialization_alias="errorMessage")


class DataclassByAlias(HttpErrorBody):
    status_code: int = Field(serialization_alias="statusCode")
    inner: AliasedDataclass

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        inner = AliasedDataclass(error_message=str(error))
        return cls(status_code=error.status_code, inner=inner)


# A typed dict's own setting is not what decides how it is written, so each
# of these is given the opposite of the model it is used in.
class TypedByAlias(TypedDict):
    __pydantic_config__ = ConfigDict(serialize_by_alias=True)  # type: ignore[misc]

    error_message: Annotated[str, Field(serialization_alias="errorMessage")]


class TypedByName(TypedDict):
    __pydantic_config__ = ConfigDict(serialize_by_alias=False)  # type: ignore[misc]

    error_message: Annotated[str, Field(serialization_alias="errorMessage")]


@dataclasses.dataclass
class PlainDataclass:
    error_message: Annotated[str, Field(serialization_alias="errorMessage")]


@dataclasses.dataclass
class ConfiguredDataclass:
    """A plain dataclass that carries a pydantic config is written by that config."""

    __pydantic_config__ = ConfigDict(serialize_by_alias=True)

    error_message: Annotated[str, Field(serialization_alias="errorMessage")]


class TypedDictUnderFieldNames(HttpErrorBody):
    """A typed dict and a plain dataclass are written as the model around them is."""

    typed: TypedByAlias
    plain: PlainDataclass
    configured: ConfiguredDataclass

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        message = str(error)
        return cls(
            typed={"error_message": message},
            plain=PlainDataclass(error_message=message),
            configured=ConfiguredDataclass(error_message=message),
        )


class TypedDictUnderAliases(HttpErrorBody):
    model_config = ConfigDict(serialize_by_alias=True)

    typed: TypedByName
    plain: PlainDataclass

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        message = str(error)
        return cls(
            typed={"error_message": message},
            plain=PlainDataclass(error_message=message),
        )


class SharedTypedDictUnderAliases(HttpErrorBody):
    """Used twice, the typed dict is defined once, outside the model that holds it."""

    model_config = ConfigDict(serialize_by_alias=True)

    first: TypedByName
    second: TypedByName

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        typed: TypedByName = {"error_message": str(error)}
        return cls(first=typed, second=typed)


class InnerByAlias(HttpErrorBody):
    """Each model is written by alias only where its own config says so."""

    status_code: int = Field(serialization_alias="statusCode")
    inner: AliasedInner

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        inner = AliasedInner(error_message=str(error))
        return cls(status_code=error.status_code, inner=inner)


class OuterByAlias(HttpErrorBody):
    model_config = ConfigDict(serialize_by_alias=True)

    status_code: int = Field(serialization_alias="statusCode")
    inner: PlainInner

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        inner = PlainInner(error_message=str(error))
        return cls(status_code=error.status_code, inner=inner)


class NestedByAlias(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    first: TypedByName
    second: TypedByName
    third: PlainDataclass
    fourth: PlainDataclass


class SharedUnderANestedModel(HttpErrorBody):
    """What is shared is written as the model that holds it is, not as the root is."""

    status_code: int = Field(serialization_alias="statusCode")
    nested: NestedByAlias

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        typed: TypedByName = {"error_message": str(error)}
        plain = PlainDataclass(error_message=str(error))
        nested = NestedByAlias(first=typed, second=typed, third=plain, fourth=plain)
        return cls(status_code=error.status_code, nested=nested)


class Node(HttpErrorBody):
    """A model that refers to itself is defined apart from its uses too."""

    model_config = ConfigDict(serialize_by_alias=True)

    error_message: str = Field(serialization_alias="errorMessage")
    child: Node | None = None

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        return cls(error_message=str(error), child=cls(error_message="child"))


class SerializedProblems(ValidationErrorBody):
    count: int
    total: int
    internal: str = Field(default="not for the client", exclude=True)
    inner: AliasedInner

    @field_serializer("count")
    def _as_text(self, value: int) -> str:
        return str(value)

    @field_serializer("total", when_used="json")
    def _total_as_text(self, value: int) -> str:
        return str(value)

    @computed_field
    @property
    def kind(self) -> str:
        return "validation_error"

    @classmethod
    def from_errors(cls, errors: list[dict[str, Any]]) -> Self:
        inner = AliasedInner(error_message=errors[0]["msg"])
        return cls(count=len(errors), total=len(errors), inner=inner)


def _api() -> HattoriAPI:
    api = HattoriAPI()

    @api.post("/items")
    def create_item(request, item: Item) -> Item:
        return item

    return api


def _assert_documented_as_sent(document, name, response):
    """The body validates, and at every level its keys are the documented ones."""
    validate_response(document, "/api/items", response, method="post")
    _assert_same_keys(
        document, document["components"]["schemas"][name], response.json()
    )


def _assert_same_keys(document, schema, body):
    assert set(schema["properties"]) == set(body)
    assert set(schema["required"]) == set(body)
    for key, value in body.items():
        ref = schema["properties"][key].get("$ref")
        if ref and isinstance(value, dict):
            nested = document["components"]["schemas"][ref.rsplit("/", 1)[-1]]
            _assert_same_keys(document, nested, value)


@pytest.mark.parametrize(
    ("model", "body"),
    [
        (Serialized, {"status": "400"}),
        (SerializedForJsonOnly, {"status": 400}),
        (SerializedByBuiltins, {"status": 400, "padded": 400}),
        (Computed, {"detail": MESSAGE, "kind": "http_error"}),
        (Aliased, {"error_message": MESSAGE}),
        (SerializedByAlias, {"errorMessage": MESSAGE}),
        (Excluded, {"detail": MESSAGE}),
        (InnerByAlias, {"status_code": 400, "inner": {"errorMessage": MESSAGE}}),
        (DataclassByAlias, {"status_code": 400, "inner": {"errorMessage": MESSAGE}}),
        (OuterByAlias, {"statusCode": 400, "inner": {"error_message": MESSAGE}}),
        (
            TypedDictUnderFieldNames,
            {
                "typed": {"error_message": MESSAGE},
                "plain": {"error_message": MESSAGE},
                "configured": {"errorMessage": MESSAGE},
            },
        ),
        (
            TypedDictUnderAliases,
            {"typed": {"errorMessage": MESSAGE}, "plain": {"errorMessage": MESSAGE}},
        ),
        (
            SharedTypedDictUnderAliases,
            {"first": {"errorMessage": MESSAGE}, "second": {"errorMessage": MESSAGE}},
        ),
        (
            SharedUnderANestedModel,
            {
                "status_code": 400,
                "nested": {
                    "first": {"errorMessage": MESSAGE},
                    "second": {"errorMessage": MESSAGE},
                    "third": {"errorMessage": MESSAGE},
                    "fourth": {"errorMessage": MESSAGE},
                },
            },
        ),
    ],
)
def test_http_error_model_is_documented_as_it_is_sent(model, body):
    original = get_http_error_model()
    set_http_error_model(model)
    try:
        api = _api()
        document = export_contract(api)

        response = TestClient(api).post("/items", data=b"{")

        assert response.status_code == 400
        assert response.json() == body
        _assert_documented_as_sent(document, model.__name__, response)
    finally:
        set_http_error_model(original)


def test_model_that_refers_to_itself_is_defined_once_as_it_is_written():
    # Generated directly: the OpenAPI validator the other tests export through
    # does not terminate on a schema that refers to itself.
    schema = Node.model_json_schema(
        mode="serialization", schema_generator=DumpedModelJsonSchema, by_alias=False
    )

    assert schema["$ref"] == "#/$defs/Node"
    assert set(schema["$defs"]) == {"Node"}
    properties = schema["$defs"]["Node"]["properties"]
    assert set(properties) == {"errorMessage", "child"}
    assert properties["child"]["anyOf"][0] == {"$ref": "#/$defs/Node"}
    dumped = Node.from_error(HttpError(400, MESSAGE)).model_dump()
    assert dumped == {
        "errorMessage": MESSAGE,
        "child": {"errorMessage": "child", "child": None},
    }


def test_validation_error_model_is_documented_as_it_is_sent():
    original = get_validation_error_model()
    set_validation_error_model(SerializedProblems)
    try:
        api = _api()
        document = export_contract(api)

        response = TestClient(api).post("/items", json={})

        assert response.status_code == 422
        assert response.json() == {
            "count": "1",
            "total": 1,
            "inner": {"errorMessage": "Field required"},
            "kind": "validation_error",
        }
        _assert_documented_as_sent(document, "SerializedProblems", response)
    finally:
        set_validation_error_model(original)


def test_default_models_are_documented_as_before():
    schemas = export_contract(_api())["components"]["schemas"]

    assert schemas["HttpErrorResponse"] == {
        "properties": {"detail": {"title": "Detail", "type": "string"}},
        "required": ["detail"],
        "title": "HttpErrorResponse",
        "type": "object",
    }
    assert schemas["ValidationErrorResponse"]["required"] == ["detail"]
    assert schemas["ValidationErrorDetail"]["required"] == ["loc", "msg", "type"]
    assert schemas["ValidationErrorDetail"]["additionalProperties"] is True
