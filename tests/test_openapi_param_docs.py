"""What a parameter's documentation keeps from the field that declares it."""

from enum import Enum
from typing import Annotated

from pydantic import Field

from hattori import HattoriAPI, Path, Query, Schema


class Segment(str, Enum):
    """How results are grouped."""

    DAILY = "daily"
    MONTHLY = "monthly"


class Report(Schema):
    segment: Annotated[
        Segment,
        Field(description="Size of each period.", examples=["monthly"]),
    ]
    other: Segment
    start: Annotated[str, Field(examples=["2026-08-01"])]


class ReportOut(Schema):
    segment: Segment


api = HattoriAPI()


@api.get("/reports/{report_id}")
def get_report(
    request,
    report_id: Annotated[str, Path(description="ID of the report.", examples=["Rp1"])],
    filters: Query[Report],
    limit: Annotated[int, Query(examples=[25])] = 10,
) -> ReportOut:
    return ReportOut(segment=filters.segment)


def _parameters() -> dict:
    operation = api.get_openapi_schema()["paths"]["/api/reports/{report_id}"]["get"]
    return {parameter["name"]: parameter for parameter in operation["parameters"]}


def test_enum_field_of_a_schema_keeps_its_own_description_and_example():
    parameters = _parameters()

    assert parameters["segment"]["description"] == "Size of each period."
    assert parameters["segment"]["schema"]["examples"] == ["monthly"]
    assert parameters["segment"]["schema"]["enum"] == ["daily", "monthly"]
    # A field that declares nothing still shows the enum's own description...
    assert parameters["other"]["description"] == "How results are grouped."
    assert "examples" not in parameters["other"]["schema"]
    # ...and the enum's shared definition is left as the enum declared it.
    component = api.get_openapi_schema()["components"]["schemas"]["Segment"]
    assert component["description"] == "How results are grouped."
    assert "examples" not in component


def test_a_list_of_examples_stays_on_the_schema():
    # JSON Schema's `examples` is a list of values. On an OpenAPI parameter the
    # same word means a map of named Example Objects, so a list is not copied up.
    for name, example in (
        ("report_id", "Rp1"),
        ("start", "2026-08-01"),
        ("limit", 25),
        ("segment", "monthly"),
    ):
        parameter = _parameters()[name]
        assert parameter["schema"]["examples"] == [example]
        assert "examples" not in parameter
    assert _parameters()["report_id"]["description"] == "ID of the report."


def test_named_examples_belong_only_on_the_parameter():
    named_examples = {"monthly": {"summary": "Monthly report", "value": "monthly"}}

    class Filters(Schema):
        segment: Segment = Query(..., examples=named_examples)
        other: Segment

    api = HattoriAPI()

    @api.get("/named")
    def named(
        request,
        filters: Query[Filters],
        limit: int = Query(1, examples={"one": {"value": 1}}),
    ) -> str:
        return ""

    schema = api.get_openapi_schema()
    parameters = {
        p["name"]: p for p in schema["paths"]["/api/named"]["get"]["parameters"]
    }
    assert parameters["segment"]["examples"] == named_examples
    assert parameters["limit"]["examples"] == {"one": {"value": 1}}
    for name in ("segment", "other", "limit"):
        assert "examples" not in parameters[name]["schema"]
    # The reusable query model must also remain a valid JSON Schema.
    assert (
        "examples"
        not in schema["components"]["schemas"]["Filters"]["properties"]["segment"]
    )
    assert "examples" not in schema["components"]["schemas"]["Segment"]
    assert api.get_openapi_schema() == schema
