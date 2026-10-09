"""An endpoint, an auth callback and a permission's ``check`` declare their
responses as arms of a return annotation, and all three are read the same way:
through a ``type`` alias, plain or generic, and with a generic ``APIReturn``."""

from typing import Annotated, ClassVar, Generic, Literal, TypeVar

import pytest
from pydantic import PlainSerializer

from hattori import JSONL, ApiError, APIReturn, HattoriAPI, Schema
from hattori.security import HttpBearer
from hattori.security.permissions import BasePermission
from hattori.testing import TestClient

T = TypeVar("T")


class Out(Schema):
    id: int


class Other(Schema):
    name: str


class Missing(ApiError):
    code = 404
    error_code = "missing"
    message = "missing"


class Rejected(APIReturn[T], Generic[T]):
    code: ClassVar[int] = 403
    description: ClassVar[str] = "Refused, with the reason."


type OrMissing = Out | Missing
type Or[T] = T | Missing
type Nested = OrMissing | None
type StreamOrMissing = JSONL[Out] | Missing
type Body = Out | Other

MISSING = {"code": "missing", "message": "missing"}
REFUSAL = {"application/json": {"schema": {"$ref": "#/components/schemas/Out"}}}


def _endpoint(annotation, result):
    api = HattoriAPI()

    def view(request):
        return result

    view.__annotations__["return"] = annotation
    api.get("/view")(view)
    return api


def _responses(api):
    return api.get_openapi_schema()["paths"]["/api/view"]["get"]["responses"]


@pytest.mark.parametrize(
    "annotation",
    [OrMissing, Or[Out], Nested, OrMissing | Other],
    ids=["alias", "generic-alias", "alias-in-an-alias", "alias-beside-an-arm"],
)
def test_endpoint_responses_are_read_through_a_type_alias(annotation):
    api = _endpoint(annotation, Missing())

    response = TestClient(api).get("/view")

    assert response.status_code == 404
    assert response.json() == MISSING
    assert _responses(api)[404]["content"] == {
        "application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}
    }


def test_stream_is_read_through_a_type_alias():
    api = _endpoint(StreamOrMissing, iter([Out(id=1)]))

    response = TestClient(api).get("/view")

    assert response.content == b'{"id":1}\n'
    assert list(_responses(api)[200]["content"]) == ["application/jsonl"]
    assert 404 in _responses(api)


@pytest.mark.parametrize("annotation", [Body, Body | Missing], ids=["alone", "beside"])
def test_alias_that_names_a_body_is_documented_under_its_own_name(annotation):
    api = _endpoint(annotation, Out(id=1))

    assert TestClient(api).get("/view").json() == {"id": 1}
    assert _responses(api)[200]["content"] == {
        "application/json": {"schema": {"$ref": "#/components/schemas/Body"}}
    }


@pytest.mark.parametrize(
    "annotation", [str | Missing, Or[str]], ids=["union", "generic-alias"]
)
def test_auth_responses_are_read_like_an_endpoints(annotation):
    class Bearer(HttpBearer):
        def authenticate(self, request, token):
            return Missing()

    Bearer.authenticate.__annotations__["return"] = annotation
    api = _endpoint(Out, Out(id=1))
    api.auth = [Bearer()]

    response = TestClient(api).get("/view", headers={"Authorization": "Bearer t"})

    assert response.status_code == 404
    assert response.json() == MISSING
    assert 404 in _responses(api)


def test_generic_response_from_an_auth_is_documented_and_sent():
    def by_token(request) -> str | Rejected[Out]:
        return Rejected(Out(id=7))

    api = _endpoint(Out, Out(id=1))
    api.auth = [by_token]

    response = TestClient(api).get("/view")

    assert response.status_code == 403
    assert response.json() == {"id": 7}
    assert _responses(api)[403] == {
        "description": "Refused, with the reason.",
        "content": REFUSAL,
    }


def test_generic_response_from_a_permission_is_documented_and_sent():
    class Checked(BasePermission):
        def check(self, request) -> Literal[True] | Rejected[Out]:
            return Rejected(Out(id=7))

    api = _endpoint(Out, Out(id=1))
    api.permissions = [Checked()]

    response = TestClient(api).get("/view")

    assert response.status_code == 403
    assert response.json() == {"id": 7}
    # The check cannot return a falsy result, so the 403 is this one alone.
    assert _responses(api)[403] == {
        "description": "Refused, with the reason.",
        "content": REFUSAL,
    }


type Replies = Body | Missing
type AnnotatedMissing = Annotated[Missing, "noted"]


def test_body_alias_behind_a_response_alias_keeps_its_own_name():
    api = _endpoint(Replies, Out(id=1))

    assert TestClient(api).get("/view").json() == {"id": 1}
    assert _responses(api)[200]["content"] == {
        "application/json": {"schema": {"$ref": "#/components/schemas/Body"}}
    }
    assert 404 in _responses(api)


@pytest.mark.parametrize(
    "annotation",
    [
        Annotated[Missing, "noted"] | Out,
        AnnotatedMissing | Out,
        Annotated[Out | Missing, "noted"],
    ],
    ids=["annotated", "annotated-behind-an-alias", "annotated-union"],
)
def test_response_written_in_annotated_is_read_as_the_response(annotation):
    class Bearer(HttpBearer):
        def authenticate(self, request, token):
            return Missing()

    Bearer.authenticate.__annotations__["return"] = annotation
    endpoint = _endpoint(annotation, Missing())
    behind_auth = _endpoint(Out, Out(id=1))
    behind_auth.auth = [Bearer()]

    for api in (endpoint, behind_auth):
        response = TestClient(api).get("/view", headers={"Authorization": "Bearer t"})
        assert response.status_code == 404
        assert response.json() == MISSING
        assert 404 in _responses(api)


def test_metadata_on_a_body_is_the_bodys_to_keep():
    shouted = Annotated[str, PlainSerializer(lambda value: value.upper())]
    api = _endpoint(shouted | Missing, "ok")

    assert TestClient(api).get("/view").json() == "OK"


def test_metadata_on_the_body_of_an_auth_response_is_kept():
    shouted = Annotated[str, PlainSerializer(lambda value: value.upper())]

    def by_token(request) -> str | Rejected[shouted]:
        return Rejected("no")

    api = _endpoint(Out, Out(id=1))
    api.auth = [by_token]

    assert TestClient(api).get("/view").json() == "NO"


def test_verdict_written_in_annotated_is_read_as_the_verdict():
    class Checked(BasePermission):
        def check(self, request) -> Annotated[Literal[True], "noted"] | Rejected[Out]:
            return True

    assert Checked().can_return_falsy is False
