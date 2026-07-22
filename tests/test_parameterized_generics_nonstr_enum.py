"""Parameterizing a generic Schema with a non-string enum value must not crash.

``Schema.__class_getitem__`` builds a clean schema name from the Literal/enum
args. Enum members whose ``.value`` is not a string (e.g. an ``IntEnum`` status
code) must be stringified for the name, not concatenated raw.
"""

from enum import Enum
from typing import Generic, Literal, TypeVar

from hattori import APIReturn, HattoriAPI, Schema

C = TypeVar("C", default=object)


class ErrorResponse(Schema, Generic[C]):
    code: C
    message: str


class IntStatus(int, Enum):
    GONE = 410


def test_literal_int_enum_parameterization_names_schema():
    model = ErrorResponse[Literal[IntStatus.GONE]]
    assert model.__name__ == "ErrorResponse_410"


def test_int_enum_error_response_in_openapi():
    class Gone(APIReturn[ErrorResponse[Literal[IntStatus.GONE]]]):
        code = 410

    api = HattoriAPI()

    @api.get("/thing")
    def thing(request) -> ErrorResponse[Literal["ok"]] | Gone:  # noqa: ARG001
        pass

    schema = api.get_openapi_schema()
    # Schema builds without crashing and the 410 body is documented.
    assert 410 in schema["paths"]["/api/thing"]["get"]["responses"]
    assert "ErrorResponse_410" in schema["components"]["schemas"]
