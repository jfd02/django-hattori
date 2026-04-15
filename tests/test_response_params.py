from typing import Optional

from hattori import HattoriAPI, Schema
from hattori.testing import TestClient

api = HattoriAPI()


class SomeResponse(Schema):
    field1: Optional[int] = 1
    field2: Optional[str] = "default value"
    field3: Optional[int] = None


@api.get("/test-no-params")
def op_no_params(request) -> SomeResponse:
    return {}  # should set defaults from schema


@api.get("/test-unset", exclude_unset=True)
def op_exclude_unset(request) -> SomeResponse:
    return {"field3": 10}


@api.get("/test-defaults", exclude_defaults=True)
def op_exclude_defaults(request) -> SomeResponse:
    # changing only field1
    return {"field1": 3, "field2": "default value"}


@api.get("/test-none", exclude_none=True)
def op_exclude_none(request) -> SomeResponse:
    # setting field1 to None to exclude
    return {"field1": None, "field2": "default value"}


client = TestClient(api)


def test_arguments():
    assert client.get("/test-no-params").json() == {
        "field1": 1,
        "field2": "default value",
        "field3": None,
    }
    assert client.get("/test-unset").json() == {"field3": 10}
    assert client.get("/test-defaults").json() == {"field1": 3}
    assert client.get("/test-none").json() == {"field2": "default value"}
