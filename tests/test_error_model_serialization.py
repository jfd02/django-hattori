"""The framework's error bodies are documented the way they are sent.

The ``HttpError`` and 422 models are dumped in JSON mode, whatever the renderer,
and by alias if the model's own config sets ``serialize_by_alias``. The spec is
generated with the same two answers, so a serializer, a computed field, an
alias or an excluded field cannot make it describe a body the API never sends.
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
    ConfigError,
    HttpError,
    ValidationErrorBody,
    get_http_error_model,
    get_validation_error_model,
    set_http_error_model,
    set_validation_error_model,
)
from hattori.renderers import BaseRenderer
from hattori.responses import json_dumps
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
    """The JSON renderer has the body dumped in JSON mode, where this runs."""

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
    """Neither serializer says when it is used, and both run in JSON mode."""

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


# What decides how a body is written is the config of the body itself, so each
# of these is given the opposite of the body it is used in.
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
    """A plain dataclass that carries a pydantic config of its own."""

    __pydantic_config__ = ConfigDict(serialize_by_alias=True)

    error_message: Annotated[str, Field(serialization_alias="errorMessage")]


class TypedDictUnderFieldNames(HttpErrorBody):
    """Everything inside a body is written as the body is."""

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
    """Used twice, the typed dict is defined once, and written as the body is."""

    model_config = ConfigDict(serialize_by_alias=True)

    first: TypedByName
    second: TypedByName

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        typed: TypedByName = {"error_message": str(error)}
        return cls(first=typed, second=typed)


class InnerByAlias(HttpErrorBody):
    """A model inside the body is written as the body is, whatever its own config."""

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


class AliasedHolder(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    typed: TypedByName


class PlainHolder(BaseModel):
    typed: TypedByName


class NestedByAlias(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    first: TypedByName
    second: TypedByName
    third: PlainDataclass
    fourth: PlainDataclass


class SharedUnderANestedModel(HttpErrorBody):
    """What is shared is written as the body is, not as the model that holds it."""

    status_code: int = Field(serialization_alias="statusCode")
    nested: NestedByAlias

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        typed: TypedByName = {"error_message": str(error)}
        plain = PlainDataclass(error_message=str(error))
        nested = NestedByAlias(first=typed, second=typed, third=plain, fourth=plain)
        return cls(status_code=error.status_code, nested=nested)


class SharedBetweenTwoPolicies(HttpErrorBody):
    """One typed dict under two models that would each write it differently."""

    aliased: AliasedHolder
    plain: PlainHolder

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        typed: TypedByName = {"error_message": str(error)}
        return cls(aliased=AliasedHolder(typed=typed), plain=PlainHolder(typed=typed))


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
        (SerializedForJsonOnly, {"status": "400"}),
        (SerializedByBuiltins, {"status": "400", "padded": "0400"}),
        (Computed, {"detail": MESSAGE, "kind": "http_error"}),
        (Aliased, {"error_message": MESSAGE}),
        (SerializedByAlias, {"errorMessage": MESSAGE}),
        (Excluded, {"detail": MESSAGE}),
        (InnerByAlias, {"status_code": 400, "inner": {"error_message": MESSAGE}}),
        (DataclassByAlias, {"status_code": 400, "inner": {"error_message": MESSAGE}}),
        (OuterByAlias, {"statusCode": 400, "inner": {"errorMessage": MESSAGE}}),
        (
            TypedDictUnderFieldNames,
            {
                "typed": {"error_message": MESSAGE},
                "plain": {"error_message": MESSAGE},
                "configured": {"error_message": MESSAGE},
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
                    "first": {"error_message": MESSAGE},
                    "second": {"error_message": MESSAGE},
                    "third": {"error_message": MESSAGE},
                    "fourth": {"error_message": MESSAGE},
                },
            },
        ),
        (
            SharedBetweenTwoPolicies,
            {
                "aliased": {"typed": {"error_message": MESSAGE}},
                "plain": {"typed": {"error_message": MESSAGE}},
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

        response = TestClient(api).post(
            "/items", data=b"{", content_type="application/json"
        )

        assert response.status_code == 400
        assert response.json() == body
        _assert_documented_as_sent(document, model.__name__, response)
    finally:
        set_http_error_model(original)


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
            "total": "1",
            "inner": {"error_message": "Field required"},
            "kind": "validation_error",
        }
        _assert_documented_as_sent(document, "SerializedProblems", response)
    finally:
        set_validation_error_model(original)


def test_error_body_is_dumped_in_json_mode_whatever_the_renderer():
    class PythonRenderer(BaseRenderer):
        media_type = "application/json"
        serialization_mode = "python"

        def render(self, request, data, *, response_status):
            return json_dumps(data)

    original = get_http_error_model()
    set_http_error_model(SerializedForJsonOnly)
    try:
        api = HattoriAPI(renderer=PythonRenderer())

        @api.post("/items")
        def create_item(request, item: Item) -> Item:
            return item

        document = export_contract(api)
        response = TestClient(api).post(
            "/items", data=b"{", content_type="application/json"
        )

        assert response.json() == {"status": "400"}
        _assert_documented_as_sent(document, "SerializedForJsonOnly", response)
    finally:
        set_http_error_model(original)


def test_error_body_that_refers_to_itself_is_documented_as_it_is_sent():
    class Node(HttpErrorBody):
        model_config = ConfigDict(serialize_by_alias=True)

        error_message: str = Field(serialization_alias="errorMessage")
        child: Node | None = None

        @classmethod
        def from_error(cls, error: HttpError) -> Self:
            return cls(error_message=str(error), child=cls(error_message="child"))

    original = get_http_error_model()
    set_http_error_model(Node)
    try:
        api = _api()
        # Read off the spec directly: the OpenAPI validator the other tests
        # export through does not terminate on a schema that refers to itself.
        document = api.get_openapi_schema()
        response = TestClient(api).post(
            "/items", data=b"{", content_type="application/json"
        )

        assert response.json() == {
            "errorMessage": MESSAGE,
            "child": {"errorMessage": "child", "child": None},
        }
        documented = document["paths"]["/api/items"]["post"]["responses"][400]
        assert documented["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/Node"
        }
        node = document["components"]["schemas"]["Node"]
        assert set(node["properties"]) == {"errorMessage", "child"}
        assert node["properties"]["child"]["anyOf"][0] == {
            "$ref": "#/components/schemas/Node"
        }
        assert "Node_2" not in document["components"]["schemas"]
    finally:
        set_http_error_model(original)


@pytest.mark.parametrize(
    ("base", "install"),
    [
        (HttpErrorBody, set_http_error_model),
        (ValidationErrorBody, set_validation_error_model),
    ],
)
@pytest.mark.parametrize(
    "shape",
    ["built", "build-deferred", "not-built-yet"],
)
def test_error_model_that_overrides_model_dump_is_refused(base, install, shape):
    # The body is sent as pydantic's serializer writes it, so an override could
    # only be ignored.
    class Redacting(base):
        model_config = ConfigDict(defer_build=shape == "build-deferred")

        secret: str = "secret"
        if shape == "not-built-yet":
            later: NotDefinedYet  # noqa: F821

        def model_dump(self, **kwargs):
            return {"detail": "safe"}

    with pytest.raises(ConfigError, match="Redacting overrides model_dump"):
        install(Redacting)


def test_model_dump_override_inside_an_error_body_is_not_called():
    # Pydantic never calls one there, so the body is sent and documented as
    # its serializer writes it.
    class Inner(BaseModel):
        detail: str

        def model_dump(self, **kwargs):
            return {"detail": "overridden"}

    class Holding(HttpErrorBody):
        inner: Inner

        @classmethod
        def from_error(cls, error: HttpError) -> Self:
            return cls(inner=Inner(detail=str(error)))

    original = get_http_error_model()
    set_http_error_model(Holding)
    try:
        api = _api()
        document = export_contract(api)

        response = TestClient(api).post(
            "/items", data=b"{", content_type="application/json"
        )

        assert response.json() == {"inner": {"detail": MESSAGE}}
        _assert_documented_as_sent(document, "Holding", response)
    finally:
        set_http_error_model(original)


def test_error_model_that_refers_to_a_type_defined_later_is_installed():
    class RefersAhead(HttpErrorBody):
        later: list[DefinedLater]

        @classmethod
        def from_error(cls, error: HttpError) -> Self:
            return cls(later=[DefinedLater(detail=str(error))])

    class DefinedLater(BaseModel):
        detail: str

    original = get_http_error_model()
    set_http_error_model(RefersAhead)
    try:
        response = TestClient(_api()).post(
            "/items", data=b"{", content_type="application/json"
        )

        assert response.json() == {"later": [{"detail": MESSAGE}]}
    finally:
        set_http_error_model(original)


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
