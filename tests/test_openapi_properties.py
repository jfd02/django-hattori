"""Generate API declarations and check their exported contracts against runtime."""

from decimal import Decimal
from functools import reduce
from operator import or_
from typing import Annotated
from urllib.parse import urlencode

from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from pydantic import (
    ConfigDict,
    Field,
    StringConstraints,
    create_model,
    field_serializer,
    field_validator,
)

from hattori import ApiError, ErrorBody, HattoriAPI, Query, Schema
from hattori.testing import TestClient
from tests.openapi_contract import export_contract, validate_response

MODEL_NAMES = st.sampled_from(["Item", "Item_2", "Item_by_alias", "Other"])
WIRE_NAMES = st.sampled_from(["value", "wire_value", "examples", "properties"])
SCALARS = [(int, 1), (str, "text"), (bool, True), (list[int], [1, 2])]


@st.composite
def model_graphs(draw):
    nodes = draw(
        st.lists(
            st.tuples(MODEL_NAMES, st.integers(0, 3), WIRE_NAMES, st.booleans()),
            min_size=1,
            max_size=4,
        )
    )
    order = draw(st.permutations(range(len(nodes))))
    return nodes, order


def _constant_view(annotation, value):
    def view(request) -> annotation:
        return value

    return view


@given(graph=model_graphs(), by_alias=st.booleans())
def test_model_graphs_survive_collisions_and_registration_order(graph, by_alias):
    nodes, order = graph
    models, values = [], []
    for index, (name, scalar, alias, nested) in enumerate(nodes):
        annotation, sample = SCALARS[scalar]
        fields = {"value": (annotation, Field(serialization_alias=alias))}
        value = {"value": sample}
        if index and nested:
            fields["child"] = (models[-1], ...)
            fields["children"] = (list[models[-1]], ...)
            value.update(child=values[-1], children=[values[-1]])
        model = create_model(name, __base__=Schema, **fields)
        models.append(model)
        values.append(model(**value))

    api = HattoriAPI()
    for index in order:
        api.get(
            f"/item/{index}",
            operation_id=f"item_{index}",
            url_name=f"item_{index}",
            by_alias=by_alias,
        )(_constant_view(models[index], values[index]))
    document = export_contract(api)
    assert export_contract(api) == document
    client = TestClient(api)
    for index in range(len(nodes)):
        response = client.get(f"/item/{index}")
        assert response.json() == values[index].model_dump(
            mode="json", by_alias=by_alias
        )
        validate_response(document, f"/api/item/{index}", response)


@given(
    alias=WIRE_NAMES,
    optional_argument=st.booleans(),
    nested=st.booleans(),
    optional_nested=st.booleans(),
    collection=st.booleans(),
    csv=st.booleans(),
    values=st.lists(st.integers(-20, 20), min_size=1, max_size=4),
)
def test_query_schema_parameters_describe_the_values_runtime_accepts(
    alias, optional_argument, nested, optional_nested, collection, csv, values
):
    field_type = list[int] if collection else int
    inner = create_model(
        "Filters",
        __base__=Schema,
        value=(field_type, Query(..., alias=alias, explode=not csv)),
    )
    model = inner
    if nested:
        model = create_model(
            "Outer",
            __base__=Schema,
            inner=(
                inner | None if optional_nested else inner,
                None if optional_nested else ...,
            ),
        )
    annotation = model | None if optional_argument else model
    api = HattoriAPI()

    @api.get("/query")
    def query(
        request, filters: annotation = Query(None if optional_argument else ...)
    ) -> field_type:
        return filters.inner.value if nested else filters.value

    document = export_contract(api)
    parameters = document["paths"]["/api/query"]["get"]["parameters"]
    assert [p["name"] for p in parameters] == [alias]
    expected = values if collection else values[0]
    Draft202012Validator({
        **parameters[0]["schema"],
        "components": document["components"],
    }).validate(expected)
    encoded = ",".join(map(str, values)) if collection and csv else expected
    client = TestClient(api)
    response = client.get("/query?" + urlencode({alias: encoded}, doseq=True))
    assert response.status_code == 200
    assert response.json() == expected
    validate_response(document, "/api/query", response)
    invalid = client.get("/query?" + urlencode({alias: "not-an-integer"}))
    assert invalid.status_code == 422
    validate_response(document, "/api/query", invalid)


@given(
    kind=st.sampled_from(["integer", "set", "bytes", "decimal"]),
    value=st.integers(-20, 20),
    nullable=st.booleans(),
    use_null=st.booleans(),
    defaulted=st.booleans(),
    omit_default=st.booleans(),
    json_serializer=st.booleans(),
    plain_validator=st.booleans(),
    by_alias=st.booleans(),
    alias=WIRE_NAMES,
    flags=st.tuples(st.booleans(), st.booleans(), st.booleans()),
    model_instance=st.booleans(),
)
def test_serialized_responses_match_their_schema(
    kind,
    value,
    nullable,
    use_null,
    defaulted,
    omit_default,
    json_serializer,
    plain_validator,
    by_alias,
    alias,
    flags,
    model_instance,
):
    annotation, sample = {
        "integer": (int, value),
        "set": (set[int], {value}),
        "bytes": (bytes, str(value).encode()),
        "decimal": (Decimal, Decimal(value)),
    }[kind]
    if nullable:
        annotation |= None
        if use_null:
            sample = None
    validators = {}
    if plain_validator and kind == "integer":

        @field_validator("value", mode="plain")
        @classmethod
        def validate_value(cls, value):
            return value

        validators["validate_value"] = validate_value
    if json_serializer:

        @field_serializer("value", when_used="json")
        def as_text(self, value) -> str:
            return str(value)

        validators["as_text"] = as_text
    model = create_model(
        "Payload",
        __config__=ConfigDict(json_schema_serialization_defaults_required=True),
        __validators__=validators,
        value=(
            annotation,
            Field(default=sample if defaulted else ..., serialization_alias=alias),
        ),
    )
    supplied = {} if defaulted and omit_default else {"value": sample}
    instance = model(**supplied)
    exclude_none, exclude_defaults, exclude_unset = flags
    options = dict(
        by_alias=by_alias,
        exclude_none=exclude_none,
        exclude_defaults=exclude_defaults,
        exclude_unset=exclude_unset,
    )
    api = HattoriAPI()
    api.get("/payload", **options)(
        _constant_view(model, instance if model_instance else supplied)
    )
    document = export_contract(api)
    response = TestClient(api).get("/payload")
    assert response.json() == instance.model_dump(mode="json", **options)
    validate_response(document, "/api/payload", response)


@given(
    errors=st.lists(
        st.tuples(MODEL_NAMES, st.sampled_from([400, 409, 422])), min_size=1, max_size=4
    ),
    by_alias=st.booleans(),
    alias=WIRE_NAMES,
    strict=st.booleans(),
    validation_alias=st.booleans(),
    normalize=st.booleans(),
)
def test_error_unions_document_every_runtime_variant(
    errors, by_alias, alias, strict, validation_alias, normalize
):
    body = create_model(
        "Body",
        __base__=ErrorBody,
        code=(
            Annotated[str, StringConstraints(strict=strict, to_upper=normalize)],
            Field(strict=strict, alias="error_code" if validation_alias else None),
        ),
        message=(
            str,
            Field(alias=alias if validation_alias else None, serialization_alias=alias),
        ),
    )
    variants = [
        type(
            name,
            (ApiError,),
            {
                "code": status,
                "error_code": f"error_{index}",
                "message": f"Message {index}",
            },
            body=body,
        )
        for index, (name, status) in enumerate(errors)
    ]
    annotation = reduce(or_, variants)
    api = HattoriAPI()

    @api.get("/error", by_alias=by_alias)
    def error(request, index: int) -> annotation:
        return variants[index % len(variants)]()

    document = export_contract(api)
    client = TestClient(api)
    for index in range(len(variants)):
        response = client.get(f"/error?index={index}")
        code_field = "error_code" if by_alias and validation_alias else "code"
        assert response.json()[code_field] == f"error_{index}"
        validate_response(document, "/api/error", response)
    validate_response(document, "/api/error", client.get("/error?index=invalid"))
