"""A decimal's schema documents which strings it accepts.

Pydantic 2.14 stopped adding this pattern by default. The spec keeps it, so a
client reads the same digit limits the API validates.
"""

import re
import string
from decimal import Decimal
from typing import Annotated

from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import Field, TypeAdapter, ValidationError

from hattori import Body, HattoriAPI, Schema

Amount = Annotated[Decimal, Field(max_digits=6, decimal_places=2)]


class Payment(Schema):
    amount: Amount
    rate: Decimal


api = HattoriAPI()


@api.post("/payments")
def pay(request, body: Body[Payment]) -> None: ...


def _string_branch(field: dict) -> dict:
    # A request accepts a decimal as a JSON number or a string; the pattern
    # describes the string.
    return next(option for option in field["anyOf"] if option["type"] == "string")


def _properties() -> dict:
    return api.get_openapi_schema()["components"]["schemas"]["Payment"]["properties"]


def test_constrained_decimal_documents_its_pattern():
    pattern = _string_branch(_properties()["amount"])["pattern"]

    assert re.search(pattern, "1234.56")
    assert not re.search(pattern, "12345.6")
    assert not re.search(pattern, "1.234")


def test_unconstrained_decimal_documents_its_pattern():
    pattern = _string_branch(_properties()["rate"])["pattern"]

    assert re.search(pattern, "-0.000001")
    assert re.search(pattern, "1e5")
    assert not re.search(pattern, "1.2.3")


@st.composite
def fixed_point_strings(draw: st.DrawFn) -> str:
    sign = draw(st.sampled_from(["", "-", "+"]))
    integer = draw(st.text(alphabet=string.digits, min_size=1, max_size=8))
    fraction = draw(st.text(alphabet=string.digits, min_size=0, max_size=4))
    return f"{sign}{integer}.{fraction}" if fraction else f"{sign}{integer}"


@example(value="0001234.5600")
@example(value="NaN")
@given(value=st.one_of(fixed_point_strings(), st.sampled_from(["Infinity", "-", "."])))
def test_pattern_agrees_with_validation(value: str) -> None:
    try:
        TypeAdapter(Amount).validate_python(value)
        accepted = True
    except ValidationError:
        accepted = False

    pattern = _string_branch(Payment.json_schema()["properties"]["amount"])["pattern"]
    assert bool(re.search(pattern, value)) == accepted
