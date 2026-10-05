"""How error response bodies are documented in the OpenAPI schema."""

from enum import Enum
from typing import Literal

import pytest
from pydantic import AliasChoices, AliasPath, ConfigDict, Field, ValidationError

from hattori import ApiError, ErrorBody, HattoriAPI, NotFound, Router, Schema
from hattori.testing import TestClient
from tests.openapi_contract import export_contract, resolve, validate_response


class ItemError(Enum):
    NOT_FOUND = "item_not_found"
    GONE = "item_gone"


@pytest.mark.parametrize("enum_error", [False, True])
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("by_alias", [False, True])
def test_custom_error_fields_keep_validation_and_wire_contract(
    enum_error, strict, by_alias
):
    class CustomBody(ErrorBody):
        model_config = ConfigDict(extra="forbid", validate_by_name=False)
        code: str = Field(strict=strict, alias="error_code", description="Catalog code")
        message: str = Field(alias="detail", min_length=2)
        retry_after: int = Field(alias="retryAfter", gt=0)

    parent = NotFound[Literal[ItemError.NOT_FOUND]] if enum_error else ApiError

    class CustomError(parent, body=CustomBody):
        code = 404
        error_code = "item_not_found"
        message = "Item not found"

    body_type = CustomError.__hattori_response_body__
    original_metadata = CustomBody.model_fields["code"].metadata
    assert any(getattr(item, "strict", None) is strict for item in original_metadata)
    assert CustomBody.model_config["validate_by_name"] is False
    with pytest.raises(ValidationError):
        body_type(error_code="wrong", detail="Invalid", retryAfter=1)
    with pytest.raises(ValidationError):
        CustomError("x", retryAfter=1)
    with pytest.raises(ValidationError):
        CustomError(retryAfter=0)

    api = HattoriAPI()

    @api.get("/error", by_alias=by_alias)
    def error(request) -> CustomError:
        return CustomError(retryAfter=1)

    document = export_contract(api)
    response = TestClient(api).get("/error")
    assert response.status_code == 404
    expected = (
        {"error_code": "item_not_found", "detail": "Item not found", "retryAfter": 1}
        if by_alias
        else {"code": "item_not_found", "message": "Item not found", "retry_after": 1}
    )
    assert response.json() == expected
    validate_response(document, "/api/error", response)
    ref = document["paths"]["/api/error"]["get"]["responses"]["404"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    properties = resolve(document, ref)["properties"]
    code_field = properties["error_code" if by_alias else "code"]
    assert code_field["const"] == "item_not_found"
    assert code_field["description"] == "Catalog code"
    assert properties["detail" if by_alias else "message"]["examples"] == [
        "Item not found"
    ]


@pytest.mark.parametrize(
    "alias",
    ["input_code", AliasChoices("input_code", "legacy"), AliasPath("error", "code")],
)
def test_error_construction_supports_validation_aliases(alias):
    class CustomBody(ErrorBody):
        code: str = Field(validation_alias=alias, serialization_alias="errorCode")
        message: str = Field(validation_alias=AliasPath("error", "message"))

    class CustomError(ApiError, body=CustomBody):
        code = 400
        error_code = "custom"
        message = "Custom error"

    assert CustomError().value.model_dump(by_alias=True) == {
        "errorCode": "custom",
        "message": "Custom error",
    }


class Item(Schema):
    id: int


class ItemNotFound(NotFound[Literal[ItemError.NOT_FOUND]]):
    message = "Item not found"


class RateLimited(ApiError):
    code = 429
    error_code = "rate_limited"
    message = "Slow down"


class Unavailable(ApiError):
    """Its message is written per response, so there is nothing fixed to show."""

    code = 503
    error_code = "unavailable"


class TracedBody(ErrorBody):
    code: str = Field(description="A code from our own catalog.")
    trace_id: str


class Traced(ApiError, body=TracedBody):
    code = 500
    error_code = "traced"
    message = "Something broke"


first = Router()
second = Router()


@first.get("/items/{item_id}")
def get_item(
    request, item_id: int
) -> Item | ItemNotFound | RateLimited | Unavailable | Traced:
    return Item(id=item_id)


def _same_name_different_wording() -> type:
    # A second router declaring its own error with the same name and wire code.
    class ItemNotFound(NotFound[Literal[ItemError.NOT_FOUND]]):
        message = "No such item."

    return ItemNotFound


OtherItemNotFound = _same_name_different_wording()


@second.delete("/items/{item_id}")
def delete_item(request, item_id: int) -> Item | OtherItemNotFound:  # type: ignore[valid-type]
    return Item(id=item_id)


api = HattoriAPI()
api.add_router("", first)
api.add_router("", second)


def _schemas() -> dict:
    return api.get_openapi_schema()["components"]["schemas"]


def test_error_fields_are_described():
    for name in ("ItemNotFound", "RateLimited", "Unavailable"):
        properties = _schemas()[name]["properties"]
        assert properties["code"]["description"].startswith("Stable, machine-readable")
        assert properties["message"]["description"].startswith("Human-readable")


def test_narrowing_the_code_keeps_a_custom_body_s_description():
    properties = _schemas()["Traced"]["properties"]

    assert properties["code"] == {
        "const": "traced",
        "description": "A code from our own catalog.",
        "title": "Code",
        "type": "string",
    }
    assert "trace_id" in properties


def test_declared_message_is_the_example():
    assert _schemas()["ItemNotFound"]["properties"]["message"]["examples"] == [
        "Item not found"
    ]
    assert _schemas()["RateLimited"]["properties"]["message"]["examples"] == [
        "Slow down"
    ]
    assert _schemas()["Traced"]["properties"]["message"]["examples"] == [
        "Something broke"
    ]


def test_an_error_without_a_fixed_message_gets_no_example():
    assert "examples" not in _schemas()["Unavailable"]["properties"]["message"]


def test_wording_does_not_split_errors_that_share_a_name_and_code():
    schemas = _schemas()

    # Both operations point at one component, documented with the message of
    # the error that was registered first.
    assert "ItemNotFound_2" not in schemas
    paths = api.get_openapi_schema()["paths"]["/api/items/{item_id}"]
    for method in ("get", "delete"):
        assert "#/components/schemas/ItemNotFound" in str(paths[method]["responses"])
    assert schemas["ItemNotFound"]["properties"]["message"]["examples"] == [
        "Item not found"
    ]


def test_the_message_sent_is_unaffected():
    assert ItemNotFound().value.model_dump() == {
        "code": "item_not_found",
        "message": "Item not found",
    }
    assert OtherItemNotFound().value.message == "No such item."


@pytest.mark.parametrize("same_operation", [True, False])
def test_same_named_errors_keep_their_examples_after_component_renaming(same_operation):
    def make_error(wire_code, wording):
        class Conflict(ApiError):
            code = 409
            error_code = wire_code
            message = wording

        return Conflict

    first_error = make_error("first", "First conflict")
    second_error = make_error("second", "Second conflict")
    api = HattoriAPI()

    if same_operation:

        @api.get("/both")
        def both(request) -> first_error | second_error:
            return first_error()

    else:

        @api.get("/first")
        def first(request) -> first_error:
            return first_error()

        @api.get("/second")
        def second(request) -> second_error:
            return second_error()

    schemas = api.get_openapi_schema()["components"]["schemas"]
    assert len(schemas) == 2
    assert {
        schema["properties"]["code"]["const"]: schema["properties"]["message"][
            "examples"
        ]
        for schema in schemas.values()
    } == {"first": ["First conflict"], "second": ["Second conflict"]}


@pytest.mark.parametrize("alias_code", [True, False])
def test_error_examples_follow_serialization_aliases(alias_code):
    class AliasedBody(ErrorBody):
        code: str = Field(serialization_alias="errorCode" if alias_code else None)
        message: str = Field(serialization_alias="detail")

    class AliasedError(ApiError, body=AliasedBody):
        code = 400
        error_code = "aliased"
        message = "Something went wrong"

    api = HattoriAPI()

    @api.get("/plain", by_alias=False)
    def plain(request) -> AliasedError:
        return AliasedError()

    @api.get("/aliased", by_alias=True)
    def aliased(request) -> AliasedError:
        return AliasedError()

    schema = api.get_openapi_schema()
    for path, field in (("plain", "message"), ("aliased", "detail")):
        ref = schema["paths"][f"/api/{path}"]["get"]["responses"][400]["content"][
            "application/json"
        ]["schema"]["$ref"]
        body = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]
        assert body["properties"][field]["examples"] == [AliasedError.message]


def test_explicit_error_message_examples_take_precedence():
    class ExampleBody(ErrorBody):
        message: str = Field(examples=["An explicit example"])

    class ExampleError(ApiError, body=ExampleBody):
        code = 400
        error_code = "example"
        message = "The declared message"

    api = HattoriAPI()

    @api.get("/example")
    def example(request) -> ExampleError:
        return ExampleError()

    schema = api.get_openapi_schema()["components"]["schemas"]["ExampleError"]
    assert schema["properties"]["message"]["examples"] == ["An explicit example"]
