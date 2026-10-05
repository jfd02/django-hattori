"""Validate exported OpenAPI and exercise the contracts against actual responses."""

from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from typing import Annotated, Literal

import pytest
from pydantic import (
    ConfigDict,
    Field,
    Json,
    computed_field,
    create_model,
    field_serializer,
    field_validator,
)

from hattori import ApiError, Body, File, Form, HattoriAPI, Path, Query, Schema
from hattori.errors import ConfigError
from hattori.files import UploadedFile
from hattori.testing import TestClient
from tests.openapi_contract import export_contract, resolve, validate_response


@pytest.mark.parametrize("annotation", [Json, Json[str | None]])
def test_exclude_none_with_decoded_json_field(annotation):
    class Payload(Schema):
        value: annotation

    api = HattoriAPI()

    @api.get("/value", exclude_none=True)
    def view(request) -> Payload:
        return Payload(value="null")

    document = export_contract(api)
    response = TestClient(api).get("/value")
    assert response.json() == {}
    validate_response(document, "/api/value", response)


@pytest.mark.parametrize("serialize_field", [False, True])
@pytest.mark.parametrize("as_instance", [False, True])
def test_exclude_none_with_plain_field_validator(serialize_field, as_instance):
    class Payload(Schema):
        value: str | None

        @field_validator("value", mode="plain")
        @classmethod
        def parse(cls, value):
            return None if value == "" else value

        if serialize_field:

            @field_serializer("value")
            def serialize_value(self, value) -> str:
                return value or "missing"

    api = HattoriAPI()

    @api.get("/value", exclude_none=True)
    def view(request) -> Payload:
        return Payload(value="") if as_instance else {"value": ""}

    document = export_contract(api)
    response = TestClient(api).get("/value")
    assert response.json() == {}
    validate_response(document, "/api/value", response)


@pytest.mark.parametrize("as_dataclass", [False, True])
@pytest.mark.parametrize("by_alias", [False, True])
@pytest.mark.parametrize("serialize_field", [False, True])
def test_exclude_none_with_nullable_type_aliases(
    as_dataclass, by_alias, serialize_field
):
    type Nullable = str | None

    class Fields:
        value: Nullable = Field(serialization_alias="wireValue")

        if serialize_field:

            @field_serializer("value")
            def serialize_value(self, value) -> str:
                return value or "missing"

    if as_dataclass:
        Payload = dataclass(Fields)
    else:

        class Payload(Fields, Schema):
            pass

    api = HattoriAPI()

    @api.get("/full", by_alias=by_alias)
    def full(request) -> Payload:
        return Payload(value=None)

    @api.get("/excluded", exclude_none=True, by_alias=by_alias)
    def excluded(request) -> Payload:
        return Payload(value=None)

    document = export_contract(api)
    client = TestClient(api)
    for path in ("full", "excluded"):
        response = client.get(f"/{path}")
        expected = "missing" if serialize_field else None
        assert response.json() == (
            {}
            if path == "excluded"
            else {"wireValue" if by_alias else "value": expected}
        )
        validate_response(document, f"/api/{path}", response)


@pytest.mark.parametrize("custom_error", [False, True])
def test_json_parse_errors_are_documented_alongside_declared_400(custom_error):
    class BadRequest(ApiError):
        code = 400
        error_code = "bad_request"
        message = "Rejected"

    api = HattoriAPI()
    response_type = int | BadRequest if custom_error else int

    @api.post("/body")
    def body(request, value: int = Body(...)) -> response_type:
        return BadRequest() if custom_error else value

    document = export_contract(api)
    client = TestClient(api)
    malformed = client.post("/body", body=b"{")
    assert malformed.status_code == 400
    validate_response(document, "/api/body", malformed, method="post")
    validate_response(
        document, "/api/body", client.post("/body", json=1), method="post"
    )


def test_non_json_operations_do_not_document_json_parse_errors():
    api = HattoriAPI()

    @api.post("/form")
    def form(request, text: str = Form(...)) -> str:
        return text

    assert "400" not in export_contract(api)["paths"]["/api/form"]["post"]["responses"]


@pytest.mark.parametrize("reverse", [False, True])
def test_nullable_query_enum_collisions(reverse):
    first = Enum("Choice", {"A": "a", "B": "b"}, type=str)
    second = Enum("Choice", {"X": "x", "Y": "y"}, type=str)
    api = HattoriAPI()

    def one(request, choice: first | None = Query(None)) -> str:
        return choice.value if choice else ""

    def two(request, choice: second | None = Query(None)) -> str:
        return choice.value if choice else ""

    routes = [("/one", one), ("/two", two)]
    for path, view in reversed(routes) if reverse else routes:
        api.get(path)(view)
    document = export_contract(api)
    client = TestClient(api)
    for path, valid, invalid in (("one", "a", "x"), ("two", "x", "a")):
        parameter = document["paths"][f"/api/{path}"]["get"]["parameters"][0]
        ref = parameter["schema"]["anyOf"][0]["$ref"]
        assert valid in resolve(document, ref)["enum"]
        assert invalid not in resolve(document, ref)["enum"]
        assert client.get(f"/{path}?choice={valid}").status_code == 200
        assert client.get(f"/{path}?choice={invalid}").status_code == 422


def test_discriminator_mappings_follow_component_renames():
    def models(value_type):
        class Cat(Schema):
            kind: Literal["cat"]
            value: value_type

        class Dog(Schema):
            kind: Literal["dog"]
            value: value_type

        class Pet(Schema):
            animal: Annotated[Cat | Dog, Field(discriminator="kind")]

        return Pet

    first, second = models(int), models(str)
    api = HattoriAPI()

    @api.get("/one")
    def one(request) -> first:
        return {"animal": {"kind": "cat", "value": 1}}

    @api.get("/two")
    def two(request) -> second:
        return {"animal": {"kind": "dog", "value": "text"}}

    document = export_contract(api)
    client = TestClient(api)
    for path in ("one", "two"):
        validate_response(document, f"/api/{path}", client.get(f"/{path}"))


@pytest.mark.parametrize("reverse", [False, True])
def test_generated_component_names_do_not_overwrite_declared_names(reverse):
    first = create_model("Thing", value=(int, ...))
    second = create_model("Thing", value=(str, ...))
    numbered = create_model("Thing_2", value=(bool, ...))

    class Out(Schema):
        text: second
        flag: numbered

    api = HattoriAPI()

    def one(request) -> first:
        return {"value": 1}

    def two(request) -> Out:
        return {"text": {"value": "text"}, "flag": {"value": True}}

    routes = [("/one", one), ("/two", two)]
    for path, view in reversed(routes) if reverse else routes:
        api.get(path)(view)
    document = export_contract(api)
    client = TestClient(api)
    for path in ("one", "two"):
        validate_response(document, f"/api/{path}", client.get(f"/{path}"))


def test_schema_renaming_preserves_payloads_and_external_references():
    class Thing(Schema):
        value: int

    first = Thing
    payload = {
        "$ref": "#/components/schemas/Thing",
        "discriminator": {"mapping": {"x": "#/components/schemas/Thing"}},
    }

    class Thing(Schema):
        value: str
        data: dict = Field(examples=[payload])

    api = HattoriAPI()

    @api.get("/one")
    def one(request) -> first:
        return {"value": 1}

    @api.get("/two")
    def two(request) -> Thing:
        return {"value": "a", "data": payload}

    document = export_contract(api)
    data = document["components"]["schemas"]["Thing_2"]["properties"]["data"]
    assert data["examples"][0] == payload
    # An external schema with the same last path segment must stay external.
    from hattori.openapi.schema import OpenAPISchema

    schema = {
        "$ref": "https://example.com/Thing",
        "default": payload,
        "example": payload,
        "x-payload": payload,
    }
    OpenAPISchema(HattoriAPI(), "").rename_schema_refs(schema, {"Thing": "Thing_2"})
    assert schema["$ref"] == "https://example.com/Thing"
    assert schema["default"] == schema["example"] == schema["x-payload"] == payload


@pytest.mark.parametrize("status_key", [200, "200"])
def test_response_overrides_preserve_the_generated_body(status_key):
    api = HattoriAPI()

    @api.get(
        "/extra",
        openapi_extra={
            "responses": {
                status_key: {"description": "Custom success"},
                "default": {"description": "Other error"},
            }
        },
    )
    def extra(request) -> str:
        return "ok"

    document = export_contract(api)
    responses = document["paths"]["/api/extra"]["get"]["responses"]
    assert responses["200"]["description"] == "Custom success"
    assert "default" in responses
    validate_response(document, "/api/extra", TestClient(api).get("/extra"))


@pytest.mark.parametrize("custom_validation", [False, True])
def test_explicit_422_also_documents_framework_validation_errors(custom_validation):
    class BusinessError(ApiError):
        code = 422
        error_code = "business"
        message = "Business rule failed"

    class OtherError(ApiError):
        code = 422
        error_code = "other"
        message = "Another business rule failed"

    api = HattoriAPI()

    @api.get("/items")
    def items(request, count: int) -> str | BusinessError | OtherError:
        if count < -1:
            return OtherError()
        return BusinessError() if count < 0 else "ok"

    from hattori.errors import get_validation_error_model, set_validation_error_model
    from tests.test_validation_error_model import Problem

    original = get_validation_error_model()
    try:
        if custom_validation:
            set_validation_error_model(Problem)
        document = export_contract(api)
        client = TestClient(api)
        for value in ("1", "-1", "-2", "invalid"):
            validate_response(
                document, "/api/items", client.get(f"/items?count={value}")
            )
    finally:
        set_validation_error_model(original)


@pytest.mark.parametrize(
    "option", ["exclude_none", "exclude_defaults", "exclude_unset"]
)
@pytest.mark.parametrize("by_alias", [False, True])
def test_response_exclusions_are_specific_to_the_operation(option, by_alias):
    class Child(Schema):
        model_config = ConfigDict(json_schema_serialization_defaults_required=True)
        value: str | None = Field(serialization_alias="wireValue")
        label: str = "default"
        tags: list[str] = Field(default_factory=list)

        @computed_field
        @property
        def computed(self) -> str:
            return "computed"

    class Out(Schema):
        child: Child
        children: list[Child]

    api = HattoriAPI()

    @api.get("/full", by_alias=by_alias)
    def full(request) -> Out:
        return Out(child=Child(value=None), children=[Child(value=None)])

    @api.get("/excluded", by_alias=by_alias, **{option: True})
    def excluded(request) -> Out:
        return full(request)

    @api.post("/echo", by_alias=by_alias, **{option: True})
    def echo(request, payload: Out) -> Out:
        return payload

    document = export_contract(api)
    client = TestClient(api)
    for path in ("full", "excluded"):
        validate_response(document, f"/api/{path}", client.get(f"/{path}"))
    input_ref = document["paths"]["/api/echo"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    input_schema = resolve(document, input_ref)
    input_child = resolve(document, input_schema["properties"]["child"]["$ref"])
    assert "value" in input_child["required"]
    response = client.post(
        "/echo", json={"child": {"value": None}, "children": [{"value": None}]}
    )
    validate_response(document, "/api/echo", response, method="post")
    full_ref = document["paths"]["/api/full"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    full_schema = resolve(document, full_ref)
    child = resolve(document, full_schema["properties"]["child"]["$ref"])
    assert set(child["required"]) == {
        "wireValue" if by_alias else "value",
        "label",
        "tags",
        "computed",
    }


@pytest.mark.parametrize("explicit_id", [None, "custom"])
def test_multi_method_operation_ids_are_unique(explicit_id):
    api = HattoriAPI()

    @api.api_operation(["GET", "POST"], "/multi", operation_id=explicit_id)
    def multi(request) -> str:
        return "ok"

    document = export_contract(api)
    operations = document["paths"]["/api/multi"]
    assert {op["operationId"] for op in operations.values()} == {
        f"{explicit_id or 'multi'}_get",
        f"{explicit_id or 'multi'}_post",
    }


def test_exclude_none_handles_recursive_models_and_field_serializers():
    class Node(Schema):
        name: str
        next: Node | None
        label: str | None

        @field_serializer("label")
        def serialize_label(self, value) -> str:
            return value or "missing"

        @computed_field
        @property
        def computed(self) -> str | None:
            return None

    api = HattoriAPI()

    @api.get("/node", exclude_none=True)
    def node(request) -> Node:
        return Node(name="a", next=None, label=None)

    document = export_contract(api)
    response = TestClient(api).get("/node")
    assert response.json() == {"name": "a"}
    validate_response(document, "/api/node", response)


def test_ids_supplied_through_openapi_extra_are_checked():
    api = HattoriAPI()

    @api.get("/one", openapi_extra={"operationId": "shared"})
    def one(request) -> str:
        return "ok"

    @api.get("/two", operation_id="shared")
    def two(request) -> str:
        return "ok"

    with pytest.raises(ConfigError, match="Duplicate operation_id"):
        api.get_openapi_schema()


def test_path_parameter_with_default_is_still_required():
    api = HattoriAPI()

    @api.get("/items/{item_id}")
    def item(request, item_id: int = Path(1)) -> int:
        return item_id

    document = export_contract(api)
    assert (
        document["paths"]["/api/items/{item_id}"]["get"]["parameters"][0]["required"]
        is True
    )


def test_optional_form_json_and_multipart_bodies():
    api = HattoriAPI()

    @api.post("/form")
    def form(request, text: str = Form("")) -> str:
        return text

    @api.post("/json")
    def body(request, text: str = Body(""), count: int = Body(0)) -> str:
        return text

    @api.post("/multipart")
    def multipart(
        request, text: str = Form(""), file: UploadedFile | None = File(None)
    ) -> str:
        return text

    @api.post("/required")
    def required(request, text: str = Form(...)) -> str:
        return text

    document = export_contract(api)
    client = TestClient(api)
    for path in ("form", "json", "multipart"):
        assert (
            document["paths"][f"/api/{path}"]["post"]["requestBody"]["required"]
            is False
        )
        assert client.post(f"/{path}").status_code == 200
    assert document["paths"]["/api/required"]["post"]["requestBody"]["required"] is True
    assert client.post("/required").status_code == 422


def test_nullable_query_model_documents_its_flattened_fields():
    # Minimal counterexample found by Hypothesis.
    class Filters(Schema):
        value: int

    api = HattoriAPI()

    @api.get("/query")
    def query(request, filters: Filters | None = Query(None)) -> int:
        return filters.value

    document = export_contract(api)
    assert document["paths"]["/api/query"]["get"]["parameters"][0]["name"] == "value"
    response = TestClient(api).get("/query?value=0")
    assert response.json() == 0
    validate_response(document, "/api/query", response)


@pytest.mark.parametrize("as_instance", [False, True])
def test_json_only_serializers_match_the_documented_response(as_instance):
    class Payload(Schema):
        value: int

        @field_serializer("value", when_used="json")
        def as_text(self, value) -> str:
            return str(value)

    api = HattoriAPI()

    @api.get("/payload")
    def payload(request) -> Payload:
        return Payload(value=0) if as_instance else {"value": 0}

    document = export_contract(api)
    response = TestClient(api).get("/payload")
    assert response.json() == {"value": "0"}
    validate_response(document, "/api/payload", response)


def test_sets_and_bytes_use_pydantic_json_serialization():
    class Payload(Schema):
        values: set[int]
        text: bytes

    api = HattoriAPI()

    @api.get("/payload")
    def payload(request) -> Payload:
        return Payload(values={0}, text=b"0")

    document = export_contract(api)
    response = TestClient(api).get("/payload")
    assert response.json() == {"values": [0], "text": "0"}
    validate_response(document, "/api/payload", response)


@pytest.mark.parametrize(
    "module",
    [
        "test_openapi_param_docs",
        "test_error_body_docs",
        "test_query_schema_csv",
        "test_schema_example_order",
        "test_validation_error_reporting",
        "test_validation_error_model",
        "test_http_errors",
        "test_body",
    ],
)
def test_existing_api_contracts(module):
    export_contract(import_module(f"tests.{module}").api)
