"""Enum-keyed HTTP error responses (HTTPError + semantic status bases)."""

from enum import Enum
from typing import ClassVar, Literal

import pytest

from hattori import (
    BadGateway,
    BadRequest,
    Conflict,
    EnumT,
    ErrorBody,
    Forbidden,
    GatewayTimeout,
    Gone,
    HattoriAPI,
    HTTPError,
    InternalServerError,
    MethodNotAllowed,
    NotFound,
    PayloadTooLarge,
    PaymentRequired,
    Schema,
    ServiceUnavailable,
    TooManyRequests,
    Unauthorized,
    UnprocessableEntity,
    get_default_error_body,
    set_default_error_body,
)
from hattori.testing import TestClient


class CreateError(Enum):
    DUPLICATE_NAME = "duplicate_name"
    GROUP_NOT_FOUND = "group_not_found"
    INVALID_INPUT = "invalid_input"


class UserOut(Schema):
    id: int
    name: str


class DuplicateName(Conflict[Literal[CreateError.DUPLICATE_NAME]]):
    message = "Already exists"


class GroupNotFound(NotFound[Literal[CreateError.GROUP_NOT_FOUND]]):
    message = "Group not found"


class InvalidInput(BadRequest[Literal[CreateError.INVALID_INPUT]]):
    message = "Bad input"


api = HattoriAPI()


@api.get("/users/{id}")
def get_user(
    request, id: int
) -> UserOut | DuplicateName | GroupNotFound | InvalidInput:
    if id == 1:
        return DuplicateName()
    if id == 2:
        return GroupNotFound()
    if id == 3:
        return InvalidInput("dynamic message")
    return UserOut(id=id, name="alice")


client = TestClient(api)


def test_wire_code_derived_from_enum_member():
    r = client.get("/users/1")
    assert r.status_code == 409
    assert r.json() == {"code": "duplicate_name", "message": "Already exists"}


def test_status_from_semantic_base():
    r = client.get("/users/2")
    assert r.status_code == 404
    assert r.json() == {"code": "group_not_found", "message": "Group not found"}


def test_runtime_message_override():
    r = client.get("/users/3")
    assert r.status_code == 400
    assert r.json() == {"code": "invalid_input", "message": "dynamic message"}


def test_happy_path_implicit_200():
    r = client.get("/users/99")
    assert r.status_code == 200
    assert r.json() == {"id": 99, "name": "alice"}


def test_error_code_set_at_class_creation():
    assert DuplicateName.error_code == "duplicate_name"
    assert GroupNotFound.error_code == "group_not_found"
    assert InvalidInput.error_code == "invalid_input"


def test_status_codes_set_on_semantic_bases():
    assert BadRequest.code == 400
    assert Unauthorized.code == 401
    assert PaymentRequired.code == 402
    assert Forbidden.code == 403
    assert NotFound.code == 404
    assert MethodNotAllowed.code == 405
    assert Conflict.code == 409
    assert Gone.code == 410
    assert PayloadTooLarge.code == 413
    assert UnprocessableEntity.code == 422
    assert TooManyRequests.code == 429
    assert InternalServerError.code == 500
    assert BadGateway.code == 502
    assert ServiceUnavailable.code == 503
    assert GatewayTimeout.code == 504


def test_openapi_includes_each_status():
    schema = api.get_openapi_schema()
    codes = set(schema["paths"]["/api/users/{id}"]["get"]["responses"].keys())
    assert {200, 400, 404, 409}.issubset(codes)


def test_openapi_response_body_uses_literal_error_code_schema():
    schema = api.get_openapi_schema()
    responses = schema["paths"]["/api/users/{id}"]["get"]["responses"]
    body_409 = responses[409]["content"]["application/json"]["schema"]
    ref = body_409["$ref"].rsplit("/", 1)[-1]
    body_schema = schema["components"]["schemas"][ref]
    assert body_schema["properties"]["code"]["const"] == "duplicate_name"
    assert body_schema["properties"]["message"]["type"] == "string"


def test_subclass_can_be_further_specialized():
    """A user-defined intermediate subclass (e.g. for shared messaging) still
    resolves the enum member when leaf-parameterized."""

    class _PaidConflict(Conflict[Literal[CreateError.DUPLICATE_NAME]]):
        message = "paid users only"

    assert _PaidConflict.error_code == "duplicate_name"
    assert _PaidConflict.code == 409


def test_http_error_module_exports():
    """All semantic bases are importable from the top-level package."""
    assert HTTPError.__name__ == "HTTPError"


# --- Bare-member parameterization (no Literal wrapper) ---


class _BareE(Enum):
    BARE_X = "bare_x"
    BARE_Y = "bare_y"


class BareConflict(Conflict[_BareE.BARE_X]):
    """Pyright auto-promotes an enum member to Literal[member] in type position,
    so the framework must accept both forms identically."""

    message = "bare X"


class WrappedConflict(Conflict[Literal[_BareE.BARE_Y]]):
    message = "wrapped Y"


def test_bare_enum_member_parameterization_works_like_literal():
    """Conflict[E.X] and Conflict[Literal[E.X]] must produce identical runtime behavior."""
    assert BareConflict.error_code == "bare_x"
    assert BareConflict.code == 409
    assert WrappedConflict.error_code == "bare_y"
    assert WrappedConflict.code == 409


def test_bare_form_works_end_to_end():
    api2 = HattoriAPI()

    @api2.get("/bare/{n}")
    def view(request, n: int) -> UserOut | BareConflict:
        if n == 0:
            return BareConflict()
        return UserOut(id=n, name="x")

    c = TestClient(api2)
    r = c.get("/bare/0")
    assert r.status_code == 409
    assert r.json() == {"code": "bare_x", "message": "bare X"}


def test_openapi_multiple_same_status_errors_are_discriminated_union():
    api2 = HattoriAPI()

    @api2.get("/conflicts/{n}")
    def view(request, n: int) -> UserOut | BareConflict | WrappedConflict:
        if n == 0:
            return BareConflict()
        if n == 1:
            return WrappedConflict()
        return UserOut(id=n, name="x")

    schema = api2.get_openapi_schema()
    body_409 = schema["paths"]["/api/conflicts/{n}"]["get"]["responses"][409][
        "content"
    ]["application/json"]["schema"]
    assert "anyOf" not in body_409
    one_of = body_409["oneOf"]
    ref_names = {item["$ref"].rsplit("/", 1)[-1] for item in one_of}
    assert ref_names == {"BareConflict", "WrappedConflict"}

    codes = {
        schema["components"]["schemas"][name]["properties"]["code"]["const"]
        for name in ref_names
    }
    assert codes == {"bare_x", "bare_y"}
    assert body_409["discriminator"] == {
        "propertyName": "code",
        "mapping": {
            "bare_x": "#/components/schemas/BareConflict",
            "bare_y": "#/components/schemas/WrappedConflict",
        },
    }


# --- Custom body shape (per-subclass, inherited, and module-level default) ---


class _RetryE(Enum):
    TOO_MANY = "too_many"
    BUSY = "busy"


class RetryBody(ErrorBody):
    retry_after: int


class TooManyRetries(TooManyRequests[Literal[_RetryE.TOO_MANY]], body=RetryBody):
    message = "Slow down"


def test_custom_body_extra_field_serialized():
    api2 = HattoriAPI()

    @api2.get("/retry")
    def view(request) -> UserOut | TooManyRetries:
        return TooManyRetries(retry_after=30)

    r = TestClient(api2).get("/retry")
    assert r.status_code == 429
    assert r.json() == {
        "code": "too_many",
        "message": "Slow down",
        "retry_after": 30,
    }


def test_custom_body_runtime_message_override():
    api2 = HattoriAPI()

    @api2.get("/retry")
    def view(request) -> UserOut | TooManyRetries:
        return TooManyRetries("custom", retry_after=5)

    r = TestClient(api2).get("/retry")
    assert r.json() == {"code": "too_many", "message": "custom", "retry_after": 5}


def test_custom_body_openapi_includes_extra_fields():
    api2 = HattoriAPI()

    @api2.get("/retry")
    def view(request) -> UserOut | TooManyRetries:
        return TooManyRetries(retry_after=1)

    schema = api2.get_openapi_schema()
    body_429 = schema["paths"]["/api/retry"]["get"]["responses"][429]["content"][
        "application/json"
    ]["schema"]
    ref = body_429["$ref"].rsplit("/", 1)[-1]
    body_schema = schema["components"]["schemas"][ref]
    assert body_schema["properties"]["code"]["const"] == "too_many"
    assert body_schema["properties"]["retry_after"]["type"] == "integer"
    assert "retry_after" in body_schema["required"]


def test_body_inherited_from_intermediate_base():
    """Setting body= on an intermediate generic base propagates to leaf classes
    without them having to repeat it."""

    from typing import TypeVar

    E = TypeVar("E", bound=Enum)

    class AppBadRequest(BadRequest[E], body=RetryBody):
        pass

    class _LeafE(Enum):
        X = "leaf_x"

    class Leaf(AppBadRequest[Literal[_LeafE.X]]):
        message = "leaf"

    instance = Leaf(retry_after=7)
    assert instance.value.model_dump() == {
        "code": "leaf_x",
        "message": "leaf",
        "retry_after": 7,
    }


def test_module_level_default_body_used_as_fallback():
    class DefaultBody(ErrorBody):
        request_id: str

    original = get_default_error_body()
    set_default_error_body(DefaultBody)
    try:

        class _DefE(Enum):
            FOO = "foo"

        class UsesDefault(BadRequest[Literal[_DefE.FOO]]):
            message = "foo!"

        instance = UsesDefault(request_id="abc-123")
        assert instance.value.model_dump() == {
            "code": "foo",
            "message": "foo!",
            "request_id": "abc-123",
        }
    finally:
        set_default_error_body(original)


def test_explicit_body_overrides_module_default():
    class DefaultBody(ErrorBody):
        request_id: str

    original = get_default_error_body()
    set_default_error_body(DefaultBody)
    try:

        class _OvrE(Enum):
            BAR = "bar"

        class Explicit(BadRequest[Literal[_OvrE.BAR]], body=RetryBody):
            message = "bar!"

        # request_id is NOT required; retry_after IS.
        with pytest.raises(Exception):
            Explicit()  # missing retry_after
        instance = Explicit(retry_after=1)
        assert "request_id" not in instance.value.model_dump()
        assert instance.value.model_dump() == {
            "code": "bar",
            "message": "bar!",
            "retry_after": 1,
        }
    finally:
        set_default_error_body(original)


# --- Semantic bases for 402 / 5xx upstream failures ---


class UpstreamError(Enum):
    PAYMENT_REQUIRED = "payment_required"
    UPSTREAM_BAD = "upstream_bad"
    UPSTREAM_DOWN = "upstream_down"
    UPSTREAM_SLOW = "upstream_slow"


class PaymentNeeded(PaymentRequired[Literal[UpstreamError.PAYMENT_REQUIRED]]):
    message = "Subscription required"


class UpstreamBad(BadGateway[Literal[UpstreamError.UPSTREAM_BAD]]):
    message = "Upstream returned garbage"


class UpstreamDown(ServiceUnavailable[Literal[UpstreamError.UPSTREAM_DOWN]]):
    message = "Upstream unavailable"


class UpstreamSlow(GatewayTimeout[Literal[UpstreamError.UPSTREAM_SLOW]]):
    message = "Upstream timed out"


upstream_api = HattoriAPI()


@upstream_api.get("/sync/{n}")
def sync_view(
    request, n: int
) -> UserOut | PaymentNeeded | UpstreamBad | UpstreamDown | UpstreamSlow:
    if n == 402:
        return PaymentNeeded()
    if n == 502:
        return UpstreamBad()
    if n == 503:
        return UpstreamDown()
    if n == 504:
        return UpstreamSlow()
    return UserOut(id=n, name="x")


upstream_client = TestClient(upstream_api)


@pytest.mark.parametrize(
    "status,code",
    [
        (402, "payment_required"),
        (502, "upstream_bad"),
        (503, "upstream_down"),
        (504, "upstream_slow"),
    ],
)
def test_upstream_semantic_bases_end_to_end(status, code):
    r = upstream_client.get(f"/sync/{status}")
    assert r.status_code == status
    assert r.json()["code"] == code


@pytest.mark.parametrize("status", [402, 502, 503, 504])
def test_upstream_semantic_bases_documented_with_const_code(status):
    schema = upstream_api.get_openapi_schema()
    body = schema["paths"]["/api/sync/{n}"]["get"]["responses"][status]["content"][
        "application/json"
    ]["schema"]
    ref = body["$ref"].rsplit("/", 1)[-1]
    assert "const" in schema["components"]["schemas"][ref]["properties"]["code"]


def test_enum_typevar_is_exported_for_custom_bases():
    """EnumT is public, so a status hattori doesn't ship can be declared
    without re-declaring the TypeVar."""

    class NotImplementedYet(HTTPError[EnumT]):
        code: ClassVar[int] = 501

    class _E(Enum):
        NOPE = "not_implemented"

    class Nope(NotImplementedYet[Literal[_E.NOPE]]):
        message = "later"

    assert Nope.code == 501
    assert Nope.error_code == "not_implemented"
    assert Nope().value.model_dump() == {
        "code": "not_implemented",
        "message": "later",
    }
