"""Schema is a thin BaseModel wrapper. No ORM coercion, no resolvers,
no dotted aliases. These tests pin that contract."""

from typing import Literal, Union

import pytest
from pydantic_core import ValidationError

from hattori import Schema


class UserSchema(Schema):
    id: int
    name: str


def test_explicit_construction():
    u = UserSchema(id=1, name="John")
    assert u.model_dump() == {"id": 1, "name": "John"}


def test_dict_validation():
    u = UserSchema.model_validate({"id": 1, "name": "John"})
    assert u.model_dump() == {"id": 1, "name": "John"}


def test_orm_style_object_is_rejected():
    """Schema intentionally does NOT accept arbitrary objects with attributes.
    Users must construct schemas explicitly from ORM data."""

    class FakeOrmUser:
        id = 1
        name = "John"

    with pytest.raises(ValidationError):
        UserSchema.model_validate(FakeOrmUser())


def test_schema_validates_assignment_and_reassigns_the_value():
    class ValidateAssignmentSchema(Schema):
        str_var: str
        model_config = {"validate_assignment": True}

    schema_inst = ValidateAssignmentSchema(str_var="test_value")
    schema_inst.str_var = "reassigned_value"
    assert schema_inst.str_var == "reassigned_value"
    with pytest.raises(ValidationError):
        schema_inst.str_var = 5  # type: ignore[assignment]


@pytest.mark.parametrize("validate_assignment", [False, None])
def test_schema_skips_validation_when_validate_assignment_False(
    validate_assignment: Union[bool, None],
):
    class ValidateAssignmentSchema(Schema):
        str_var: str
        model_config = {"validate_assignment": validate_assignment}

    inst = ValidateAssignmentSchema(str_var="test_value")
    inst.str_var = 5  # type: ignore[assignment]
    assert inst.str_var == 5


def test_literal_parameterization_produces_clean_names():
    """Parameterized generic schemas get clean OpenAPI names from Literal values."""

    class ErrorResponse(Schema):
        code: str
        message: str

    from typing import Generic, TypeVar

    C = TypeVar("C", default=str)

    class _ErrorResponse(Schema, Generic[C]):
        code: C
        message: str

    model = _ErrorResponse[Literal["not_found"]]
    assert model.__name__ == "_ErrorResponse_not_found"
