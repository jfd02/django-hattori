"""Example payloads keep the key order they were written in."""

from pydantic import Field

from hattori import HattoriAPI, Schema


class Money(Schema):
    currency: str
    amount: str


class Line(Schema):
    total: Money = Field(
        examples=[{"currency": "USD", "amount": "6.50"}],
        default_factory=lambda: Money(currency="USD", amount="0"),
    )
    parts: list[Money] = Field(
        examples=[[{"currency": "USD", "amount": "4.50", "note": {"b": 1, "a": 2}}]]
    )
    zeta: int = 0
    alpha: int = Field(0, json_schema_extra={"example": {"z": 1, "a": 2}})


api = HattoriAPI()


@api.post("/lines")
def create_line(request, line: Line) -> Line:
    return line


def test_example_objects_are_not_alphabetized():
    line = api.get_openapi_schema()["components"]["schemas"]["Line"]
    properties = line["properties"]

    assert list(properties["total"]["examples"][0]) == ["currency", "amount"]
    part = properties["parts"]["examples"][0][0]
    assert list(part) == ["currency", "amount", "note"]
    assert list(part["note"]) == ["b", "a"]
    assert list(properties["alpha"]["example"]) == ["z", "a"]


def test_schema_keywords_stay_sorted_and_fields_stay_in_declared_order():
    line = api.get_openapi_schema()["components"]["schemas"]["Line"]

    assert list(line["properties"]) == ["total", "parts", "zeta", "alpha"]
    assert list(line) == sorted(line)
    assert list(line["properties"]["zeta"]) == sorted(line["properties"]["zeta"])
