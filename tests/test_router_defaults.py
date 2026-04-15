from typing import Any, Optional

import pytest
from pydantic import Field

from hattori import HattoriAPI, Router, Schema
from hattori.testing import TestClient


class SomeResponse(Schema):
    field1: Optional[int] = 1
    field2: Optional[str] = "default value"
    field3: Optional[int] = Field(None, alias="aliased")


@pytest.mark.parametrize(
    "oparg,retdict,assertone,asserttwo",
    [
        (
            "exclude_defaults",
            {"field1": 3},
            {"field1": 3},
            {"field1": 3, "field2": "default value", "field3": None},
        ),
        (
            "exclude_unset",
            {"field2": "test"},
            {"field2": "test"},
            {"field1": 1, "field2": "test", "field3": None},
        ),
        (
            "exclude_none",
            {"field1": None, "field2": None, "aliased": 10},
            {"field3": 10},
            {"field1": None, "field2": None, "field3": 10},
        ),
        (
            "by_alias",
            {"aliased": 10},
            {"field1": 1, "field2": "default value", "aliased": 10},
            {"field1": 1, "field2": "default value", "field3": 10},
        ),
    ],
)
def test_router_defaults(oparg, retdict, assertone, asserttwo):
    """Test that the router level settings work and can be overridden at the op level"""
    api = HattoriAPI()
    router = Router(**{oparg: True})
    api.add_router("/", router)

    @router.get("/test1")
    def test1_endpoint(request) -> SomeResponse:
        return retdict

    @router.get("/test2", **{oparg: False})
    def test2_endpoint(request) -> SomeResponse:
        return retdict

    func1 = test1_endpoint
    func2 = test2_endpoint

    client = TestClient(api)

    assert getattr(func1._hattori_operation, oparg) is True
    assert getattr(func2._hattori_operation, oparg) is False

    assert client.get("/test1").json() == assertone
    assert client.get("/test2").json() == asserttwo
