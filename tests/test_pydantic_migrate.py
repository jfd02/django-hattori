import pytest
from pydantic import BaseModel, ValidationError

from hattori import Schema


class OptModel(BaseModel):
    a: int = None
    b: int | None
    c: int | None = None


class OptSchema(Schema):
    a: int = None
    b: int | None
    c: int | None = None


def test_optional_pydantic_model():
    with pytest.raises(ValidationError):
        OptModel().model_dump()

    assert OptModel(b=None).model_dump() == {"a": None, "b": None, "c": None}


def test_optional_schema():
    with pytest.raises(ValidationError):
        OptSchema().model_dump()

    assert OptSchema(b=None).model_dump() == {"a": None, "b": None, "c": None}
