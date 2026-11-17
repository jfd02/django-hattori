"""What an operation declares is what it reads, and what it may answer it sends.

Each of these was accepted when the route was declared and documented in the
spec, and then did not work: a response that is itself null under
``exclude_none``, a body or a query field read by an alias, a list anywhere but
in the query, and a form on a method Django parses none for.
"""

import pytest
from django.test import override_settings
from pydantic import AliasChoices, AliasPath, ConfigDict, Field, field_validator

from hattori import (
    JSONL,
    Body,
    Cookie,
    Form,
    HattoriAPI,
    Header,
    Path,
    Query,
    Schema,
)
from hattori.compatibility.files import FIX_MIDDLEWARE_PATH
from hattori.errors import ConfigError
from hattori.testing import TestClient
from tests.openapi_contract import export_contract


def _operation(api, path, method="get"):
    return api.get_openapi_schema()["paths"][f"/api{path}"][method]


def _parameter(api, path, name):
    [parameter] = [p for p in _operation(api, path)["parameters"] if p["name"] == name]
    return parameter


# ==========================================================================
# A response that is itself None, under exclude_none
# ==========================================================================


@pytest.mark.parametrize("flag", ["exclude_none", "exclude_unset", "exclude_defaults"])
def test_response_that_is_none_is_sent_whatever_is_excluded(flag):
    api = HattoriAPI()

    @api.get("/maybe", **{flag: True})
    def maybe(request, empty: bool = False) -> str | None:
        return None if empty else "here"

    client = TestClient(api)
    schema = _operation(api, "/maybe")["responses"][200]["content"]["application/json"]

    assert {"type": "null"} in schema["schema"]["anyOf"]
    assert client.get("/maybe?empty=true").json() is None
    assert client.get("/maybe").json() == "here"


def test_stream_item_that_is_none_is_sent_under_exclude_none():
    api = HattoriAPI()

    @api.get("/items", exclude_none=True)
    def items(request) -> JSONL[int | None]:
        yield 1
        yield None

    assert TestClient(api).get("/items").content == b"1\nnull\n"


# ==========================================================================
# A parameter read by an alias
# ==========================================================================


class Item(Schema):
    name: str


@pytest.mark.parametrize(
    "source",
    [Body(..., alias="value"), Body(..., validation_alias="value")],
    ids=["alias", "validation_alias"],
)
def test_single_body_parameter_with_an_alias_is_the_body(source):
    api = HattoriAPI()

    @api.post("/count")
    def count(request, number: int = source) -> int:
        return number

    @api.post("/item")
    def item(request, payload: Item = source) -> Item:
        return payload

    client = TestClient(api)
    body = _operation(api, "/count", "post")["requestBody"]["content"]

    # The body is the value, as the spec says: the alias names nothing in it.
    assert body["application/json"]["schema"]["type"] == "integer"
    assert client.post("/count", json=1).json() == 1
    assert client.post("/item", json={"name": "a"}).json() == {"name": "a"}
    refused = client.post("/count", json={"value": 1})
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"] == ["body"]


@pytest.mark.parametrize(
    "field",
    [Field(validation_alias="max"), Field(alias="max")],
    ids=["validation_alias", "alias"],
)
def test_query_model_field_is_read_by_the_name_the_spec_gives_it(field):
    class Filters(Schema):
        limit: int = field

    api = HattoriAPI()

    @api.get("/list")
    def listing(request, filters: Query[Filters]) -> int:
        return filters.limit

    client = TestClient(api)

    assert _parameter(api, "/list", "max")["required"] is True
    assert client.get("/list?max=3").json() == 3
    missing = client.get("/list?limit=3")
    assert missing.status_code == 422
    assert missing.json()["detail"][0]["loc"] == ["query", "max"]


@pytest.mark.parametrize(
    ("source", "sent"),
    [
        (Body(..., alias="out", validation_alias=AliasChoices("number", "old")), 3),
        (Body(..., validation_alias=AliasPath("number", 0)), [3]),
    ],
    ids=["choices", "path"],
)
def test_single_body_parameter_with_an_alias_that_is_no_name_is_read_as_before(
    source, sent
):
    api = HattoriAPI()

    @api.post("/count")
    def count(request, number: int = source) -> int:
        return number

    # Such an alias names no one key, so the body goes under the parameter's own.
    assert TestClient(api).post("/count", json=sent).json() == 3


def test_nullable_schema_nested_under_a_validation_alias_takes_its_default():
    class Span(Schema):
        low: int

    class Filters(Schema):
        span: Span | None = Field(None, validation_alias="range")
        limit: int = 0

    api = HattoriAPI()

    @api.get("/list")
    def listing(request, filters: Query[Filters]) -> dict:
        return filters.model_dump()

    client = TestClient(api)

    assert client.get("/list?limit=1").json() == {"span": None, "limit": 1}
    assert client.get("/list?low=2").json() == {"span": {"low": 2}, "limit": 0}


def test_query_model_that_does_not_validate_by_alias_is_read_by_name():
    class Filters(Schema):
        model_config = ConfigDict(validate_by_name=True, validate_by_alias=False)

        limit: int = Field(validation_alias="max")

    api = HattoriAPI()

    @api.get("/list")
    def listing(request, filters: Query[Filters]) -> int:
        return filters.limit

    client = TestClient(api)

    assert [p["name"] for p in _operation(api, "/list")["parameters"]] == ["limit"]
    assert client.get("/list?limit=3").json() == 3
    assert client.get("/list?max=3").status_code == 422


def test_body_that_does_not_validate_by_alias_documents_its_field_names():
    class Filters(Schema):
        model_config = ConfigDict(validate_by_name=True, validate_by_alias=False)

        limit: int = Field(validation_alias="max")

    api = HattoriAPI()

    @api.post("/list")
    def listing(request, filters: Filters) -> int:
        return filters.limit

    schemas = api.get_openapi_schema()["components"]["schemas"]

    assert schemas["Filters"]["properties"].keys() == {"limit"}
    assert schemas["Filters"]["required"] == ["limit"]
    assert TestClient(api).post("/list", json={"limit": 3}).json() == 3


def test_two_parameters_read_by_one_name_are_refused():
    class Filters(Schema):
        limit: int = Field(validation_alias="max")
        max: int = 0

    api = HattoriAPI()

    with pytest.raises(ConfigError, match="Duplicated name: 'max'"):

        @api.get("/list")
        def listing(request, filters: Query[Filters]) -> int:
            return filters.limit


def test_parameter_with_only_a_validation_alias_is_read_by_it():
    api = HattoriAPI()

    @api.get("/list")
    def listing(request, limit: int = Query(..., validation_alias="max")) -> int:
        return limit

    assert _parameter(api, "/list", "max")["in"] == "query"
    assert TestClient(api).get("/list?max=3").json() == 3


# ==========================================================================
# A list anywhere but in the query
# ==========================================================================


def test_list_in_a_header_is_comma_separated():
    api = HattoriAPI()

    @api.get("/tags")
    def tags(request, ids: list[int] = Header(...)) -> list[int]:
        return ids

    client = TestClient(api)
    parameter = _parameter(api, "/tags", "ids")

    # OpenAPI's default for a header is the simple style: one value, commas.
    assert parameter["schema"]["type"] == "array"
    assert "explode" not in parameter
    assert client.get("/tags", headers={"Ids": "1, 2,3"}).json() == [1, 2, 3]
    assert client.get("/tags", headers={"Ids": "7"}).json() == [7]
    assert client.get("/tags", headers={"Ids": "1,x"}).status_code == 422
    # Spaces around an item and items that are empty are not part of the list.
    assert client.get("/tags", headers={"Ids": " 1 ,,2, "}).json() == [1, 2]
    assert client.get("/tags", headers={"Ids": ""}).json() == []


@pytest.mark.parametrize(
    "annotation", [set[int], tuple[int, ...]], ids=["set", "tuple"]
)
def test_set_or_tuple_in_a_header_is_split_like_a_list(annotation):
    api = HattoriAPI()

    def tags(request, ids=Header(...)) -> list[int]:
        return sorted(ids)

    tags.__annotations__["ids"] = annotation
    api.get("/tags")(tags)

    assert TestClient(api).get("/tags", headers={"Ids": "2,1"}).json() == [1, 2]


def test_value_with_commas_that_is_no_list_is_left_whole():
    api = HattoriAPI()

    @api.get("/agent")
    def agent(request, accept: str = Header(...)) -> str:
        return accept

    sent = TestClient(api).get("/agent", headers={"Accept": "a/b, c/d"})
    assert sent.json() == "a/b, c/d"


def test_list_inside_a_header_model_is_split_too():
    class Limits(Schema):
        ids: list[int]

    api = HattoriAPI()

    @api.get("/tags")
    def tags(request, limits: Header[Limits]) -> list[int]:
        return limits.ids

    assert TestClient(api).get("/tags", headers={"Ids": "1,2"}).json() == [1, 2]


def test_validator_on_a_list_is_handed_the_list():
    seen = []

    class Limits(Schema):
        ids: list[int]

        @field_validator("ids", mode="before")
        @classmethod
        def note(cls, value):
            seen.append(value)
            return value

    api = HattoriAPI()

    @api.get("/tags")
    def tags(request, limits: Header[Limits]) -> list[int]:
        return limits.ids

    assert TestClient(api).get("/tags", headers={"Ids": "1,2"}).json() == [1, 2]
    # Not the header as it came: a validator that split it itself has to go.
    assert seen == [["1", "2"]]


def test_list_in_the_path_is_comma_separated():
    api = HattoriAPI()

    @api.get("/batch/{ids}")
    def batch(request, ids: list[int] = Path(...)) -> list[int]:
        return ids

    assert _parameter(api, "/batch/{ids}", "ids")["schema"]["type"] == "array"
    assert TestClient(api).get("/batch/1,2,3").json() == [1, 2, 3]


@pytest.mark.parametrize("explode", [True, False])
def test_list_in_a_header_or_the_path_is_documented_in_the_style_it_is_read(explode):
    api = HattoriAPI()

    @api.get("/batch/{ids}")
    def batch(
        request,
        ids: list[int] = Path(..., explode=explode),
        tags: list[int] = Header(..., explode=explode),
    ) -> list[int]:
        return ids + tags

    # Both are read comma-separated, which is the simple style OpenAPI gives
    # them by default. Form is not a style a header or a path may take.
    document = export_contract(api)
    for parameter in document["paths"]["/api/batch/{ids}"]["get"]["parameters"]:
        assert "style" not in parameter
    sent = TestClient(api).get("/batch/1,2", headers={"Tags": "3"})
    assert sent.json() == [1, 2, 3]


@pytest.mark.parametrize("explode", [True, False])
def test_list_in_a_cookie_is_comma_separated_and_documented_so(explode):
    api = HattoriAPI()

    @api.get("/seen")
    def seen(request, ids: list[int] = Cookie(..., explode=explode)) -> list[int]:
        return ids

    parameter = _parameter(api, "/seen", "ids")

    # A cookie has one value: it cannot be repeated, whatever explode says.
    assert (parameter["style"], parameter["explode"]) == ("form", False)
    assert TestClient(api).get("/seen", COOKIES={"ids": "1,2"}).json() == [1, 2]


def test_optional_list_that_is_not_sent_is_left_alone():
    api = HattoriAPI()

    @api.get("/tags")
    def tags(request, ids: list[int] | None = Header(None)) -> list[int] | None:
        return ids

    assert TestClient(api).get("/tags").json() is None


# ==========================================================================
# A form on a method Django parses none for
# ==========================================================================


def test_form_is_refused_for_an_operation_if_any_of_its_methods_cannot_read_it():
    api = HattoriAPI()

    def either(request, name: str = Form(...)) -> str:
        return name

    with override_settings(MIDDLEWARE=[]):
        with pytest.raises(ConfigError, match="read a form or a file"):
            api.api_operation(["POST", "PUT"], "/thing")(either)


def test_methods_a_form_is_refused_for_are_those_the_setting_names():
    api = HattoriAPI()

    def update(request, name: str = Form(...)) -> str:
        return name

    with override_settings(MIDDLEWARE=[], HATTORI_FIX_REQUEST_FILES_METHODS={"PUT"}):
        api.patch("/thing")(update)
        with pytest.raises(ConfigError, match="read a form or a file"):
            api.put("/thing")(update)


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_form_on_another_method_is_refused_without_the_files_middleware(method):
    api = HattoriAPI()

    def update(request, name: str = Form(...)) -> str:
        return name

    with override_settings(MIDDLEWARE=[]):
        with pytest.raises(ConfigError, match="read a form or a file"):
            getattr(api, method)("/thing")(update)


def test_form_on_another_method_is_declared_with_the_files_middleware():
    api = HattoriAPI()

    with override_settings(MIDDLEWARE=[FIX_MIDDLEWARE_PATH]):

        @api.put("/thing")
        def update(request, name: str = Form(...)) -> str:
            return name

    body = _operation(api, "/thing", "put")["requestBody"]["content"]
    assert list(body) == ["application/x-www-form-urlencoded"]


def test_form_on_post_needs_no_middleware():
    api = HattoriAPI()

    with override_settings(MIDDLEWARE=[]):

        @api.post("/thing")
        def create(request, name: str = Form(...)) -> str:
            return name

    assert TestClient(api).post("/thing", data={"name": "a"}).json() == "a"
