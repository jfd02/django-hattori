"""A value a response holds that never went through validation is refused.

An instance passes response validation as is, so one built with
``model_construct``, or changed by assignment without ``validate_assignment``,
can hold values of the wrong type: here a dict where the declared type has a
nested schema. Pydantic would only warn and write that dict out whole, fields
the declared type does not have included. The response fails instead.
"""

import pytest
from pydantic_core import PydanticSerializationError

from hattori import JSONL, HattoriAPI, Schema
from hattori.testing import TestAsyncClient, TestClient


class Profile(Schema):
    bio: str


class UserOut(Schema):
    id: int
    profile: Profile


class Wrap(Schema):
    user: UserOut


SECRET_PROFILE = {"bio": "hi", "password_hash": "secret"}


def assigned_user() -> UserOut:
    user = UserOut(id=1, profile=Profile(bio="x"))
    user.profile = dict(SECRET_PROFILE)  # not validated: stays a dict
    return user


def constructed_user() -> UserOut:
    return UserOut.model_construct(id=2, profile=dict(SECRET_PROFILE))


api = HattoriAPI()


@api.get("/constructed")
def constructed(request) -> UserOut:
    return constructed_user()


@api.get("/assigned")
def assigned(request) -> UserOut:
    return assigned_user()


@api.get("/list")
def in_a_list(request) -> list[UserOut]:
    return [constructed_user()]


@api.get("/optional")
def optional(request) -> UserOut | None:
    return constructed_user()


@api.get("/dict")
def dict_values(request) -> dict[str, UserOut]:
    return {"u": constructed_user()}


@api.get("/nested")
def nested(request) -> Wrap:
    return {"user": constructed_user()}


@api.get("/well-typed")
def well_typed(request) -> UserOut:
    return UserOut.model_construct(id=3, profile=Profile(bio="ok"))


@api.get("/stream")
def stream(request) -> JSONL[UserOut]:
    yield UserOut(id=4, profile=Profile(bio="ok"))
    yield constructed_user()


@api.get("/async")
async def async_view(request) -> list[UserOut]:
    return [constructed_user()]


client = TestClient(api)


@pytest.mark.parametrize(
    "path", ["/constructed", "/assigned", "/list", "/optional", "/dict", "/nested"]
)
def test_unvalidated_value_is_refused(path):
    with pytest.raises(PydanticSerializationError, match="Profile"):
        client.get(path)


@pytest.mark.asyncio
async def test_unvalidated_value_is_refused_by_an_async_view():
    with pytest.raises(PydanticSerializationError, match="Profile"):
        await TestAsyncClient(api).get("/async")


def test_unvalidated_stream_item_is_refused():
    with pytest.raises(PydanticSerializationError, match="Profile"):
        client.get("/stream")


def test_constructed_instance_holding_the_declared_types_is_sent():
    response = client.get("/well-typed")
    assert response.status_code == 200
    assert response.json() == {"id": 3, "profile": {"bio": "ok"}}
