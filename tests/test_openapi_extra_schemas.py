"""A ``$ref`` supplied through ``openapi_extra`` replaces the generated schema.

``openapi_extra`` is merged into an operation key by key, so a schema it
supplies refines the generated one: a description, a ``maxLength``, a nested
property's example. A ``$ref`` names another schema outright, and merged over a
generated ``$ref`` it has always taken its place. A body generated as a union
has no ``$ref`` to replace, so the supplied one ended up beside the union, in a
schema that required a body to match both.
"""

import pytest
from openapi_contract import export_contract

from hattori import ApiError, Body, HattoriAPI, Schema
from hattori.security import HttpBearer

ITEM = {"$ref": "#/components/schemas/Item"}
BAD_TOKEN = {"$ref": "#/components/schemas/BadToken"}
HTTP_ERROR = {"$ref": "#/components/schemas/HttpErrorResponse"}
JSON = "application/json"


class Item(Schema):
    name: str


class BadToken(ApiError):
    code = 401
    error_code = "bad_token"
    message = "Token invalid"


class Bearer(HttpBearer):
    def authenticate(self, request, token: str) -> str | BadToken:
        return token or BadToken()


def _operation(extra, **kwargs):
    api = HattoriAPI()

    @api.post("/items", openapi_extra=extra, **kwargs)
    def create(request, item: Item = Body(...)) -> Item:
        return item

    return export_contract(api)["paths"]["/api/items"]["post"]


def _response(status, schema, **fields):
    return {"responses": {status: {"content": {JSON: {"schema": schema}}, **fields}}}


def _schema(operation, status):
    return operation["responses"][status]["content"][JSON]["schema"]


def test_generated_union_is_what_a_ref_used_to_be_left_beside():
    # A typed 401 beside the default one is generated as an ``anyOf`` of both.
    assert _schema(_operation({}, auth=Bearer()), "401") == {
        "anyOf": [BAD_TOKEN, HTTP_ERROR]
    }


@pytest.mark.parametrize(
    "supplied",
    [ITEM, {**ITEM, "description": "The same shape as a created item"}],
)
def test_ref_replaces_a_generated_union(supplied):
    operation = _operation(_response(401, supplied), auth=Bearer())

    assert _schema(operation, "401") == supplied


def test_ref_replaces_a_generated_ref_as_it_always_did():
    operation = _operation(_response(400, ITEM))

    assert _schema(operation, "400") == ITEM


def test_replaced_schema_keeps_the_rest_of_the_generated_response():
    operation = _operation(
        _response(401, ITEM, headers={"X-Reason": {"schema": {"type": "string"}}}),
        auth=Bearer(),
    )

    response = operation["responses"]["401"]
    assert response["description"] == "Unauthorized"
    assert response["headers"] == {"X-Reason": {"schema": {"type": "string"}}}
    assert response["content"] == {JSON: {"schema": ITEM}}


def test_request_body_ref_replaces_the_generated_schema():
    extra = {"requestBody": {"content": {JSON: {"schema": HTTP_ERROR}}}}

    operation = _operation(extra)

    assert operation["requestBody"] == {
        "content": {JSON: {"schema": HTTP_ERROR}},
        "required": True,
    }


@pytest.mark.parametrize(
    "refinement",
    [
        {"description": "The item that was created"},
        {"examples": [{"name": "widget"}], "title": "Created item"},
        {"properties": {"name": {"description": "Public item name"}}},
        {"properties": {"name": {"maxLength": 40}}, "x-internal": True},
    ],
)
def test_schema_without_a_ref_still_refines_the_generated_one(refinement):
    operation = _operation(_response(200, refinement))

    assert _schema(operation, "200") == {**ITEM, **refinement}


def test_supplied_alternatives_still_extend_a_generated_union():
    operation = _operation(_response(401, {"anyOf": [ITEM]}), auth=Bearer())

    assert _schema(operation, "401") == {"anyOf": [BAD_TOKEN, HTTP_ERROR, ITEM]}


def test_ref_for_another_media_type_leaves_the_generated_one_alone():
    extra = {"responses": {200: {"content": {"text/csv": {"schema": HTTP_ERROR}}}}}

    content = _operation(extra)["responses"]["200"]["content"]

    assert content == {JSON: {"schema": ITEM}, "text/csv": {"schema": HTTP_ERROR}}


@pytest.mark.parametrize("status", [401, "401"])
def test_status_may_be_given_as_text(status):
    operation = _operation(_response(status, ITEM), auth=Bearer())

    assert _schema(operation, "401") == ITEM


@pytest.mark.parametrize(
    "extra",
    [
        {"requestBody": {"description": "The item to create"}},
        {"requestBody": {"content": {JSON: {"example": {"name": "widget"}}}}},
        {"responses": {200: {"description": "Created", "content": {JSON: {}}}}},
        {"responses": {200: {"content": {JSON: {"schema": True}}}}},
        {"responses": {200: "see the handbook", 503: {"description": "Later"}}},
        {"responses": {"default": {"content": {JSON: {"schema": HTTP_ERROR}}}}},
    ],
)
def test_extra_without_a_ref_for_a_generated_body_is_merged_as_before(extra):
    api = HattoriAPI()

    @api.post("/items", openapi_extra=extra)
    def create(request, item: Item = Body(...)) -> Item:
        return item

    operation = api.get_openapi_schema()["paths"]["/api/items"]["post"]

    assert operation["requestBody"]["content"][JSON]["schema"] == ITEM
    if isinstance(operation["responses"][200], dict):
        generated = operation["responses"][200]["content"][JSON]["schema"]
        assert generated in (ITEM, True)
