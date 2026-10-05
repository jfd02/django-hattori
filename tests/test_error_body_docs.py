"""How error response bodies are documented in the OpenAPI schema."""

from enum import Enum
from typing import Literal

from pydantic import Field

from hattori import ApiError, ErrorBody, HattoriAPI, NotFound, Router, Schema


class ItemError(Enum):
    NOT_FOUND = "item_not_found"
    GONE = "item_gone"


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
