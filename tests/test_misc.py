import copy
import uuid

import pytest
from pydantic import BaseModel


from hattori import HattoriAPI
from hattori.constants import NOT_SET
from hattori.signature.details import is_pydantic_model
from hattori.signature.utils import UUIDStrConverter
from hattori.testing import TestClient


def test_is_pydantic_model():
    class Model(BaseModel):
        x: int

    assert is_pydantic_model(Model)
    assert is_pydantic_model("instance") is False


def test_client():
    "covering everything in testclient (including invalid paths)"
    api = HattoriAPI()
    client = TestClient(api)
    with pytest.raises(Exception):  # noqa: B017
        client.get("/404")


def test_kwargs():
    api = HattoriAPI()

    @api.get("/")
    def operation(request, a: str, *args, **kwargs) -> None:
        return None

    schema = api.get_openapi_schema()
    params = schema["paths"]["/api/"]["get"]["parameters"]
    print(params)
    assert params == [  # Only `a` should be here, not kwargs
        {
            "in": "query",
            "name": "a",
            "schema": {"title": "A", "type": "string"},
            "required": True,
        }
    ]


def test_uuid_converter():
    conv = UUIDStrConverter()
    assert isinstance(conv.to_url(uuid.uuid4()), str)


def test_copy_not_set():
    assert id(NOT_SET) == id(copy.copy(NOT_SET))
    assert id(NOT_SET) == id(copy.deepcopy(NOT_SET))
