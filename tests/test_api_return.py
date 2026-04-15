from typing import ClassVar

import pytest
from pydantic import ValidationError

from hattori import APIReturn, HattoriAPI, Schema
from hattori.errors import ConfigError
from hattori.testing import TestClient

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class UserOut(Schema):
    id: int
    name: str


class ErrorBody(Schema):
    code: str
    message: str


# A user-defined base that pins code/error_code/message semantics.
class AppError(APIReturn[ErrorBody]):
    code: ClassVar[int]
    error_code: ClassVar[str]
    message: ClassVar[str] = ""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            ErrorBody(
                code=self.error_code,
                message=message if message is not None else self.message,
            )
        )


class UserNotFound(AppError):
    code = 404
    error_code = "user_not_found"
    message = "No user with that id"


class Conflict1(AppError):
    code = 409
    error_code = "conflict_one"


class Conflict2(AppError):
    code = 409
    error_code = "conflict_two"


class NoContent(APIReturn[None]):
    code = 204


api = HattoriAPI()


@api.get("/bare-200")
def bare_200(request) -> UserOut:
    return UserOut(id=1, name="john")


@api.get("/with-error/{id}")
def with_error(request, id: int) -> UserOut | UserNotFound:
    if id == 0:
        return UserNotFound()
    return UserOut(id=id, name="john")


@api.get("/multi/{kind}")
def multi(request, kind: str) -> UserOut | UserNotFound | Conflict1 | Conflict2:
    if kind == "nf":
        return UserNotFound()
    if kind == "c1":
        return Conflict1("first conflict")
    if kind == "c2":
        return Conflict2("second conflict")
    return UserOut(id=1, name="john")


@api.get("/empty/{ok}")
def empty(request, ok: bool) -> UserOut | NoContent:
    if ok:
        return NoContent(None)
    return UserOut(id=1, name="john")


client = TestClient(api)


def test_bare_type_is_implicit_200():
    r = client.get("/bare-200")
    assert r.status_code == 200
    assert r.json() == {"id": 1, "name": "john"}


def test_apireturn_subclass_uses_its_code():
    r = client.get("/with-error/0")
    assert r.status_code == 404
    assert r.json() == {"code": "user_not_found", "message": "No user with that id"}

    r = client.get("/with-error/5")
    assert r.status_code == 200
    assert r.json() == {"id": 5, "name": "john"}


def test_multiple_variants_share_code_as_union():
    r = client.get("/multi/c1")
    assert r.status_code == 409
    assert r.json() == {"code": "conflict_one", "message": "first conflict"}

    r = client.get("/multi/c2")
    assert r.status_code == 409
    assert r.json() == {"code": "conflict_two", "message": "second conflict"}

    r = client.get("/multi/nf")
    assert r.status_code == 404


def test_none_body():
    r = client.get("/empty/1")
    assert r.status_code == 204
    assert r.content == b""


def test_openapi_schema_lists_all_codes():
    schema = api.get_openapi_schema()
    assert set(schema["paths"]["/api/bare-200"]["get"]["responses"].keys()) == {200}
    assert set(schema["paths"]["/api/with-error/{id}"]["get"]["responses"].keys()) == {
        200,
        404,
        422,
    }
    multi_codes = set(schema["paths"]["/api/multi/{kind}"]["get"]["responses"].keys())
    # Conflict1 and Conflict2 collapse to a single 409 entry.
    assert multi_codes == {200, 404, 409, 422}


def test_openapi_409_body_is_union_of_error_codes():
    schema = api.get_openapi_schema()
    content = schema["paths"]["/api/multi/{kind}"]["get"]["responses"][409]["content"]
    body_schema = content["application/json"]["schema"]
    # Two Conflict classes both wrap ErrorBody, so pydantic emits a union schema.
    # We just assert something structural was generated (non-empty).
    assert body_schema


def test_missing_code_on_subclass_raises():
    bad_api = HattoriAPI()

    class Incomplete(APIReturn[ErrorBody]):
        # No code set
        pass

    with pytest.raises(ConfigError, match="concrete `code"):

        @bad_api.get("/x")
        def _view(request) -> UserOut | Incomplete:  # type: ignore[no-untyped-def]
            return UserOut(id=1, name="john")


def test_unparameterized_apireturn_raises():
    bad_api = HattoriAPI()

    class NoGeneric(APIReturn):  # type: ignore[type-arg]
        code = 418

    with pytest.raises(ConfigError, match="parameterize APIReturn"):

        @bad_api.get("/x")
        def _view(request) -> UserOut | NoGeneric:  # type: ignore[no-untyped-def]
            return UserOut(id=1, name="john")


def test_bare_value_without_200_declared_raises():
    bad_api = HattoriAPI()

    @bad_api.get("/x")
    def view(request) -> UserNotFound:
        return "oops"  # type: ignore[return-value]

    c = TestClient(bad_api)
    with pytest.raises(ConfigError, match="no 200 response is declared"):
        c.get("/x")


def test_validation_runs_on_bare_return():
    bad_api = HattoriAPI()

    @bad_api.get("/x")
    def view(request) -> UserOut:
        return {"id": "not-an-int", "name": "x"}  # type: ignore[return-value]

    c = TestClient(bad_api)
    with pytest.raises(ValidationError):
        c.get("/x")
