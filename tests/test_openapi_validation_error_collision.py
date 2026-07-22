"""Regression tests for OpenAPI component-name collisions with framework schemas.

The framework injects an auto-generated ``ValidationErrorResponse`` (and
``ValidationErrorDetail``) schema for any operation that has input params. If a
user names their own model ``ValidationErrorResponse`` the two must not clobber
each other in ``components.schemas``.
"""

from hattori import HattoriAPI, Query, Schema


def _resolve(schema, ref):
    return schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]


def test_user_model_named_validation_error_response_is_not_clobbered():
    api = HattoriAPI()

    class ValidationErrorResponse(Schema):
        custom: str

    @api.get("/ve")
    def ve(request, q: str = Query(...)) -> ValidationErrorResponse:  # noqa: ARG001
        return {"custom": "x"}

    schema = api.get_openapi_schema()
    op = schema["paths"]["/api/ve"]["get"]

    ok_ref = op["responses"][200]["content"]["application/json"]["schema"]["$ref"]
    err_ref = op["responses"][422]["content"]["application/json"]["schema"]["$ref"]

    # The two schemas are distinct components.
    assert ok_ref != err_ref

    # The 200 body is the *user's* model, not the framework error schema.
    ok_schema = _resolve(schema, ok_ref)
    assert "custom" in ok_schema["properties"]
    assert "detail" not in ok_schema["properties"]

    # The 422 body is the framework's ValidationErrorResponse.
    err_schema = _resolve(schema, err_ref)
    assert "detail" in err_schema["properties"]


def test_collision_when_framework_schema_registered_first():
    """Same collision but the framework 422 schema is emitted before the user one."""
    api = HattoriAPI()

    class ValidationErrorResponse(Schema):
        custom: str

    # This op has params -> emits the framework 422 schema first.
    @api.get("/first")
    def first(request, q: str = Query(...)) -> Schema:  # noqa: ARG001
        return {}

    # This op returns the user's identically-named model.
    @api.get("/second")
    def second(request) -> ValidationErrorResponse:  # noqa: ARG001
        return {"custom": "x"}

    schema = api.get_openapi_schema()
    ok_ref = schema["paths"]["/api/second"]["get"]["responses"][200]["content"][
        "application/json"
    ]["schema"]["$ref"]
    ok_schema = _resolve(schema, ok_ref)
    assert "custom" in ok_schema["properties"]
    assert "detail" not in ok_schema["properties"]
