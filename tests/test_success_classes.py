"""Created / Accepted / NoContent — semantic success status bases."""

from enum import Enum
from typing import Literal

from hattori import Accepted, Created, HattoriAPI, NoContent, NotFound, Schema
from hattori.testing import TestClient


class UserOut(Schema):
    id: int
    name: str


class JobOut(Schema):
    job_id: str


class GetError(Enum):
    NOT_FOUND = "not_found"


class UserNotFound(NotFound[Literal[GetError.NOT_FOUND]]):
    message = "no user"


api = HattoriAPI()


@api.post("/users")
def create_user(request) -> Created[UserOut]:
    return Created(UserOut(id=1, name="alice"))


@api.post("/jobs")
def queue_job(request) -> Accepted[JobOut]:
    return Accepted(JobOut(job_id="abc"))


@api.delete("/users/{id}")
def delete_user(request, id: int) -> NoContent | UserNotFound:
    if id == 0:
        return UserNotFound()
    return NoContent()


client = TestClient(api)


def test_created_status_and_body():
    r = client.post("/users")
    assert r.status_code == 201
    assert r.json() == {"id": 1, "name": "alice"}


def test_accepted_status_and_body():
    r = client.post("/jobs")
    assert r.status_code == 202
    assert r.json() == {"job_id": "abc"}


def test_no_content_no_body():
    r = client.delete("/users/5")
    assert r.status_code == 204
    assert r.content == b""


def test_no_content_no_arg_constructor():
    instance = NoContent()
    assert instance.value is None


def test_status_codes():
    assert Created.code == 201
    assert Accepted.code == 202
    assert NoContent.code == 204


def test_openapi_includes_correct_status_codes():
    schema = api.get_openapi_schema()
    assert 201 in schema["paths"]["/api/users"]["post"]["responses"]
    assert 202 in schema["paths"]["/api/jobs"]["post"]["responses"]
    delete_responses = schema["paths"]["/api/users/{id}"]["delete"]["responses"]
    assert 204 in delete_responses
    assert 404 in delete_responses


def test_no_content_with_error_alternative():
    r = client.delete("/users/0")
    assert r.status_code == 404
