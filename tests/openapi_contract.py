"""Reusable checks for the exported contract, independent of Hattori's generator."""

import json

from jsonschema import Draft202012Validator
from openapi_spec_validator import OpenAPIV31SpecValidator

from hattori.responses import json_dumps


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, f"Duplicate JSON key: {key}"
        result[key] = value
    return result


def resolve(document, ref):
    assert ref.startswith("#/"), f"Unexpected external reference in test API: {ref}"
    value = document
    for part in ref[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def _check_schema(document, schema):
    Draft202012Validator.check_schema(schema)
    if not isinstance(schema, dict):
        return
    if "$ref" in schema:
        resolve(document, schema["$ref"])
    discriminator = schema.get("discriminator", {})
    variants = schema.get("oneOf", schema.get("anyOf", []))
    refs = {variant["$ref"] for variant in variants if "$ref" in variant}
    for ref in discriminator.get("mapping", {}).values():
        resolve(document, ref)
        if refs:
            assert ref in refs, f"Discriminator target {ref} is not a union variant"
    for key in ("properties", "patternProperties", "$defs", "dependentSchemas"):
        for child in schema.get(key, {}).values():
            _check_schema(document, child)
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        for child in schema.get(key, []):
            _check_schema(document, child)
    for key in (
        "items",
        "additionalProperties",
        "unevaluatedProperties",
        "unevaluatedItems",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
    ):
        if key in schema:
            _check_schema(document, schema[key])


def export_contract(api):
    # Use the package's real serializer and reject duplicate object keys before
    # a JSON decoder can silently discard part of the contract.
    document = json.loads(
        json_dumps(api.get_openapi_schema()), object_pairs_hook=_unique_object
    )
    OpenAPIV31SpecValidator(document).validate()
    for schema in document["components"]["schemas"].values():
        _check_schema(document, schema)
    for methods in document["paths"].values():
        for operation in methods.values():
            for parameter in operation.get("parameters", []):
                if "schema" in parameter:
                    _check_schema(document, parameter["schema"])
            bodies = [
                operation.get("requestBody", {}),
                *operation["responses"].values(),
            ]
            for body in bodies:
                for media in body.get("content", {}).values():
                    if "schema" in media:
                        _check_schema(document, media["schema"])
    return document


def validate_response(document, path, response, method="get"):
    response_schema = document["paths"][path][method]["responses"][
        str(response.status_code)
    ]
    schema = response_schema["content"]["application/json"]["schema"]
    Draft202012Validator({**schema, "components": document["components"]}).validate(
        response.json()
    )
