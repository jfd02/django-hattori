from hattori import APIReturn, Field, HattoriAPI, Schema
from hattori.testing import TestClient


class UserOut(Schema):
    id: int
    name: str


class UserOutSub(UserOut):
    extra: str = "default"


class ErrorOut(Schema):
    detail: str


class AliasOut(Schema):
    user_name: str = Field(serialization_alias="userName")


class BadRequest(APIReturn[ErrorOut]):
    code = 400


class Created(APIReturn[UserOut]):
    code = 201


class Redirect(APIReturn[ErrorOut]):
    code = 300


class ServerError(APIReturn[ErrorOut]):
    code = 500


class NoContent(APIReturn[None]):
    code = 204


api = HattoriAPI()


@api.get("/status_dict")
def status_dict(request) -> UserOut | BadRequest:
    return {"id": 1, "name": "John"}


@api.get("/status_error")
def status_error(request) -> UserOut | BadRequest:
    return BadRequest({"detail": "bad request"})


@api.get("/status_none")
def status_none(request) -> NoContent:
    return NoContent(None)


@api.get("/status_ellipsis")
def status_ellipsis(request, code: int) -> UserOut | ServerError:
    if code == 200:
        return {"id": 1, "name": "John"}
    return ServerError({"detail": "fallback"})


@api.get("/status_code_groups")
def status_code_groups(
    request, code: int
) -> UserOut | Created | Redirect:
    if code == 200:
        return {"id": 1, "name": "John"}
    if code == 201:
        return Created({"id": 1, "name": "John"})
    return Redirect({"detail": "redirect"})


@api.get("/status_model_instance")
def status_model_instance(request) -> UserOut:
    return UserOut(id=1, name="John")


@api.get("/model_instance")
def model_instance(request) -> UserOut:
    return UserOut(id=1, name="John")


@api.get("/model_subclass")
def model_subclass(request) -> UserOut:
    return UserOutSub(id=1, name="John", extra="bonus")


@api.get("/dict_result")
def dict_result(request) -> UserOut:
    return {"id": 1, "name": "John"}


@api.get("/union_response")
def union_response(request, q: int) -> int | UserOut | BadRequest:
    if q == 0:
        return 1
    return UserOut(id=1, name="John")


@api.get("/list_response")
def list_response(request) -> list[UserOut]:
    return [{"id": 1, "name": "John"}]


@api.get("/by_alias_response", by_alias=True)
def by_alias_response(request) -> AliasOut:
    return AliasOut(user_name="Alice")


client = TestClient(api)


class TestStatusBasic:
    def test_status_with_dict(self):
        response = client.get("/status_dict")
        assert response.status_code == 200
        assert response.json() == {"id": 1, "name": "John"}

    def test_status_error_code(self):
        response = client.get("/status_error")
        assert response.status_code == 400
        assert response.json() == {"detail": "bad request"}

    def test_status_none_204(self):
        response = client.get("/status_none")
        assert response.status_code == 204
        assert response.content == b""

    def test_status_ellipsis_200(self):
        response = client.get("/status_ellipsis?code=200")
        assert response.status_code == 200
        assert response.json() == {"id": 1, "name": "John"}

    def test_status_ellipsis_fallback(self):
        response = client.get("/status_ellipsis?code=500")
        assert response.status_code == 500
        assert response.json() == {"detail": "fallback"}

    def test_status_code_groups_200(self):
        response = client.get("/status_code_groups?code=200")
        assert response.status_code == 200
        assert response.json() == {"id": 1, "name": "John"}

    def test_status_code_groups_201(self):
        response = client.get("/status_code_groups?code=201")
        assert response.status_code == 201
        assert response.json() == {"id": 1, "name": "John"}

    def test_status_code_groups_300(self):
        response = client.get("/status_code_groups?code=300")
        assert response.status_code == 300
        assert response.json() == {"detail": "redirect"}

    def test_status_wrapping_model_instance(self):
        response = client.get("/status_model_instance")
        assert response.status_code == 200
        assert response.json() == {"id": 1, "name": "John"}


class _ValidateTracker:
    """Tracks calls to Schema.model_validate without breaking it."""

    def __init__(self):
        self.call_count = 0
        self._original_func = Schema.model_validate.__func__  # type: ignore

    def __enter__(self):
        tracker = self
        original = self._original_func

        @classmethod  # type: ignore
        def tracked_validate(cls, *args, **kwargs):
            tracker.call_count += 1
            return original(cls, *args, **kwargs)

        Schema.model_validate = tracked_validate  # type: ignore
        return self

    def __exit__(self, *exc):
        if "model_validate" in Schema.__dict__:
            delattr(Schema, "model_validate")


class TestSkipRevalidation:
    """Test that the fast path skips response model_validate for matching model instances."""

    def test_model_instance_skips_validation(self):
        with _ValidateTracker() as t:
            response = client.get("/model_instance")
            assert response.status_code == 200
            assert response.json() == {"id": 1, "name": "John"}
            assert t.call_count == 0

    def test_subclass_skips_validation(self):
        with _ValidateTracker() as t:
            response = client.get("/model_subclass")
            assert response.status_code == 200
            assert response.json() == {"id": 1, "name": "John", "extra": "bonus"}
            assert t.call_count == 0

    def test_dict_goes_through_validation(self):
        with _ValidateTracker() as t:
            response = client.get("/dict_result")
            assert response.status_code == 200
            assert response.json() == {"id": 1, "name": "John"}
            assert t.call_count == 1

    def test_union_no_skip(self):
        with _ValidateTracker() as t:
            response = client.get("/union_response?q=1")
            assert response.status_code == 200
            assert response.json() == {"id": 1, "name": "John"}
            assert t.call_count == 1

    def test_list_no_skip(self):
        with _ValidateTracker() as t:
            response = client.get("/list_response")
            assert response.status_code == 200
            assert response.json() == [{"id": 1, "name": "John"}]
            assert t.call_count == 1

    def test_by_alias_serialization(self):
        response = client.get("/by_alias_response")
        assert response.status_code == 200
        assert response.json() == {"userName": "Alice"}

    def test_status_wrapping_model_skips_validation(self):
        with _ValidateTracker() as t:
            response = client.get("/status_model_instance")
            assert response.status_code == 200
            assert response.json() == {"id": 1, "name": "John"}
            assert t.call_count == 0
