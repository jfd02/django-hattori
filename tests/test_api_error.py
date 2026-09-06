"""ApiError: shipped default error pattern + escape hatch to custom body shapes."""

from typing import ClassVar

import pytest
from pydantic import BaseModel

from hattori import ApiError, APIReturn, ErrorBody, HattoriAPI, Schema
from hattori.http_errors import get_default_error_body, set_default_error_body
from hattori.testing import TestClient


class UserOut(Schema):
    id: int
    name: str


# --- Default ApiError usage ---


class UserNotFound(ApiError):
    code = 404
    error_code = "user_not_found"
    message = "No user with that id"


class PaymentFailed(ApiError):
    code = 402
    error_code = "payment_failed"  # no static message - must pass at call site


api = HattoriAPI()


@api.get("/users/{id}")
def get_user(request, id: int) -> UserOut | UserNotFound:
    if id == 0:
        return UserNotFound()  # static message from ClassVar
    if id == -1:
        return UserNotFound("runtime override message")
    return UserOut(id=id, name="alice")


@api.get("/pay/{amount}")
def pay(request, amount: int) -> UserOut | PaymentFailed:
    if amount > 100:
        return PaymentFailed(f"Insufficient balance for ${amount}")
    return UserOut(id=1, name="alice")


client = TestClient(api)


def test_apierror_static_message():
    r = client.get("/users/0")
    assert r.status_code == 404
    assert r.json() == {"code": "user_not_found", "message": "No user with that id"}


def test_apierror_runtime_override():
    r = client.get("/users/-1")
    assert r.status_code == 404
    assert r.json() == {"code": "user_not_found", "message": "runtime override message"}


def test_apierror_dynamic_message():
    r = client.get("/pay/500")
    assert r.status_code == 402
    assert r.json() == {
        "code": "payment_failed",
        "message": "Insufficient balance for $500",
    }


def test_apierror_happy_path():
    r = client.get("/users/5")
    assert r.status_code == 200
    assert r.json() == {"id": 5, "name": "alice"}


def test_apierror_shows_in_openapi():
    schema = api.get_openapi_schema()
    codes = set(schema["paths"]["/api/users/{id}"]["get"]["responses"].keys())
    assert 200 in codes and 404 in codes


# --- Escape hatch: custom body shape via APIReturn[T] directly ---


class RFC7807Problem(BaseModel):
    """Totally different error body shape. Nothing like ErrorBody."""

    type: str
    title: str
    status: int
    detail: str


class ProblemDetail(APIReturn[RFC7807Problem]):
    """User-defined base for a completely different error convention."""

    code: ClassVar[int]
    problem_type: ClassVar[str]
    title: ClassVar[str]

    def __init__(self, detail: str) -> None:
        super().__init__(
            RFC7807Problem(
                type=self.problem_type,
                title=self.title,
                status=self.code,
                detail=detail,
            )
        )


class ResourceGone(ProblemDetail):
    code = 410
    problem_type = "https://example.com/probs/gone"
    title = "Resource Gone"


class TeapotProblem(ProblemDetail):
    code = 418
    problem_type = "https://example.com/probs/teapot"
    title = "I'm a teapot"


api2 = HattoriAPI()


@api2.get("/old/{id}")
def old_resource(request, id: int) -> UserOut | ResourceGone | TeapotProblem:
    if id == 0:
        return ResourceGone("This resource has been permanently removed")
    if id == 418:
        return TeapotProblem("short and stout")
    return UserOut(id=id, name="alice")


client2 = TestClient(api2)


def test_custom_body_shape_410():
    r = client2.get("/old/0")
    assert r.status_code == 410
    assert r.json() == {
        "type": "https://example.com/probs/gone",
        "title": "Resource Gone",
        "status": 410,
        "detail": "This resource has been permanently removed",
    }


def test_custom_body_shape_418():
    r = client2.get("/old/418")
    assert r.status_code == 418
    assert r.json() == {
        "type": "https://example.com/probs/teapot",
        "title": "I'm a teapot",
        "status": 418,
        "detail": "short and stout",
    }


def test_custom_body_shape_success():
    r = client2.get("/old/5")
    assert r.status_code == 200
    assert r.json() == {"id": 5, "name": "alice"}


def test_custom_and_shipped_coexist():
    """ApiError (with ErrorBody) and a user's custom-body APIReturn can be
    used in the same app without conflict."""
    mixed = HattoriAPI()

    @mixed.get("/mixed/{id}")
    def mixed_view(request, id: int) -> UserOut | UserNotFound | ResourceGone:
        if id == 0:
            return UserNotFound()
        if id == -1:
            return ResourceGone("gone")
        return UserOut(id=id, name="alice")

    c = TestClient(mixed)

    r = c.get("/mixed/0")
    assert r.status_code == 404
    assert r.json() == {"code": "user_not_found", "message": "No user with that id"}

    r = c.get("/mixed/-1")
    assert r.status_code == 410
    assert r.json()["type"] == "https://example.com/probs/gone"

    r = c.get("/mixed/5")
    assert r.status_code == 200


def test_errorbody_shape_is_exported_and_usable_directly():
    """Users should be able to reach ErrorBody for their own APIReturn
    subclasses without going through ApiError."""
    assert ErrorBody(code="x", message="y").model_dump() == {
        "code": "x",
        "message": "y",
    }


# --- error_code is narrowed in the schema, same as HTTPError's enum member ---


def test_apierror_code_is_const_in_openapi():
    """A plain ApiError must be switchable on by a generated client: its
    ``code`` is a const, not an open string."""
    schema = api.get_openapi_schema()
    body = schema["paths"]["/api/users/{id}"]["get"]["responses"][404]["content"][
        "application/json"
    ]["schema"]
    ref = body["$ref"].rsplit("/", 1)[-1]
    body_schema = schema["components"]["schemas"][ref]
    assert body_schema["properties"]["code"]["const"] == "user_not_found"
    assert body_schema["properties"]["message"]["type"] == "string"


def test_apierror_each_subclass_gets_its_own_body_model():
    assert UserNotFound.__hattori_response_body__ is not ErrorBody
    assert PaymentFailed.__hattori_response_body__ is not ErrorBody
    assert (
        UserNotFound.__hattori_response_body__
        is not PaymentFailed.__hattori_response_body__
    )


def test_apierror_same_status_union_is_discriminated():
    """Two ApiErrors on one status become a oneOf keyed on code."""

    class TokenExpired(ApiError):
        code = 401
        error_code = "token_expired"
        message = "Expired"

    class TokenInvalid(ApiError):
        code = 401
        error_code = "token_invalid"
        message = "Invalid"

    disc_api = HattoriAPI()

    @disc_api.get("/auth/{n}")
    def auth_view(request, n: int) -> UserOut | TokenExpired | TokenInvalid:
        if n == 0:
            return TokenExpired()
        if n == 1:
            return TokenInvalid()
        return UserOut(id=n, name="x")

    schema = disc_api.get_openapi_schema()
    body_401 = schema["paths"]["/api/auth/{n}"]["get"]["responses"][401]["content"][
        "application/json"
    ]["schema"]
    assert "anyOf" not in body_401
    assert body_401["discriminator"] == {
        "propertyName": "code",
        "mapping": {
            "token_expired": "#/components/schemas/TokenExpired",
            "token_invalid": "#/components/schemas/TokenInvalid",
        },
    }


def test_apierror_wire_shape_unchanged_by_narrowing():
    """Narrowing is a schema-level change only; the JSON body is untouched."""
    r = client.get("/users/0")
    assert r.json() == {"code": "user_not_found", "message": "No user with that id"}


def test_apierror_abstract_intermediate_is_not_narrowed():
    """A base that pins only ``code`` has nothing to narrow and stays generic;
    its leaves narrow independently."""

    class AppNotFound(ApiError):
        code = 404

    class WidgetNotFound(AppNotFound):
        error_code = "widget_not_found"

    assert AppNotFound.__hattori_response_body__ is ErrorBody
    assert (
        WidgetNotFound.__hattori_response_body__.model_fields["code"].annotation
        is not str
    )
    assert WidgetNotFound().value.model_dump() == {
        "code": "widget_not_found",
        "message": "",
    }


def test_apierror_inherited_error_code_keeps_parent_body():
    """A subclass that doesn't redeclare error_code inherits the narrowed body."""

    class Specialized(UserNotFound):
        message = "different wording"

    assert Specialized.__hattori_response_body__ is (
        UserNotFound.__hattori_response_body__
    )
    assert Specialized().value.model_dump() == {
        "code": "user_not_found",
        "message": "different wording",
    }


# --- Body shape customization, matching HTTPError's contract ---


class RetryableBody(ErrorBody):
    retry_after: int


def test_apierror_body_kwarg_extends_shape():
    class RateLimited(ApiError, body=RetryableBody):
        code = 429
        error_code = "rate_limited"
        message = "Slow down"

    assert RateLimited(retry_after=30).value.model_dump() == {
        "code": "rate_limited",
        "message": "Slow down",
        "retry_after": 30,
    }
    with pytest.raises(Exception):
        RateLimited()  # retry_after is required


def test_apierror_body_kwarg_inherited_from_intermediate():
    class AppError(ApiError, body=RetryableBody):
        pass

    class Throttled(AppError):
        code = 429
        error_code = "throttled"
        message = "wait"

    assert Throttled(retry_after=1).value.model_dump() == {
        "code": "throttled",
        "message": "wait",
        "retry_after": 1,
    }


def test_apierror_uses_module_level_default_body():
    class DefaultBody(ErrorBody):
        request_id: str

    original = get_default_error_body()
    set_default_error_body(DefaultBody)
    try:

        class UsesDefault(ApiError):
            code = 400
            error_code = "uses_default"
            message = "d"

        assert UsesDefault(request_id="abc").value.model_dump() == {
            "code": "uses_default",
            "message": "d",
            "request_id": "abc",
        }
    finally:
        set_default_error_body(original)
