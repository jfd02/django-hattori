"""ApiError: shipped default error pattern + escape hatch to custom body shapes."""

from typing import ClassVar

from pydantic import BaseModel

from hattori import APIReturn, ApiError, ErrorBody, HattoriAPI, Schema
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
    error_code = "payment_failed"   # no static message - must pass at call site


api = HattoriAPI()


@api.get("/users/{id}")
def get_user(request, id: int) -> UserOut | UserNotFound:
    if id == 0:
        return UserNotFound()           # static message from ClassVar
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
    assert r.json() == {"code": "payment_failed", "message": "Insufficient balance for $500"}


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
    def mixed_view(
        request, id: int
    ) -> UserOut | UserNotFound | ResourceGone:
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
    assert ErrorBody(code="x", message="y").model_dump() == {"code": "x", "message": "y"}
