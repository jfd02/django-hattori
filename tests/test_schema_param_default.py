from pydantic import ConfigDict, Field

from hattori import Cookie, Form, HattoriAPI, Header, Query, Schema
from hattori.testing import TestClient


class Filters(Schema):
    q: str
    limit: int = 10


class Inner(Schema):
    x: int


class Outer(Schema):
    inner: Inner = Inner(x=5)
    y: int = 0


class ByName(Schema):
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=False)

    q: str = Field(alias="query")


def required_params(api, path, method="get"):
    operation = api.get_openapi_schema()["paths"][path][method]
    return {p["name"]: p["required"] for p in operation["parameters"]}


def test_query_schema_with_a_default_takes_it_when_none_of_its_fields_is_sent():
    api = HattoriAPI()

    @api.get("/annotated")
    def annotated(request, f: Query[Filters] = Filters(q="custom")) -> dict:
        return f.model_dump()

    @api.get("/param")
    def param(request, f: Filters = Query(Filters(q="custom"))) -> dict:
        return f.model_dump()

    @api.get("/factory")
    def factory(
        request, f: Filters = Query(default_factory=lambda: Filters(q="made"))
    ) -> dict:
        return f.model_dump()

    client = TestClient(api)

    for path in ("/annotated", "/param"):
        assert client.get(path).json() == {"q": "custom", "limit": 10}
        assert client.get(f"{path}?q=x").json() == {"q": "x", "limit": 10}
        # Once one of its fields is sent, it is built from what was sent.
        assert client.get(f"{path}?q=x&limit=3").json() == {"q": "x", "limit": 3}
        assert client.get(f"{path}?limit=3").status_code == 422
    assert client.get("/factory").json() == {"q": "made", "limit": 10}


def test_schema_nested_in_a_query_schema_with_a_default_takes_it():
    api = HattoriAPI()

    @api.get("/nested")
    def nested(request, f: Query[Outer]) -> dict:
        return f.model_dump()

    client = TestClient(api)

    assert client.get("/nested").json() == {"inner": {"x": 5}, "y": 0}
    assert client.get("/nested?y=2").json() == {"inner": {"x": 5}, "y": 2}
    assert client.get("/nested?x=1").json() == {"inner": {"x": 1}, "y": 0}


def test_header_cookie_and_form_schemas_with_a_default_take_it():
    api = HattoriAPI()

    @api.get("/header")
    def header(request, f: Header[Filters] = Filters(q="custom")) -> dict:
        return f.model_dump()

    @api.get("/cookie")
    def cookie(request, f: Cookie[Filters] = Filters(q="custom")) -> dict:
        return f.model_dump()

    @api.post("/form")
    def form(request, f: Form[Filters] = Filters(q="custom")) -> dict:
        return f.model_dump()

    client = TestClient(api)

    assert client.get("/header").json() == {"q": "custom", "limit": 10}
    assert client.get("/header", headers={"q": "h"}).json() == {"q": "h", "limit": 10}
    assert client.get("/cookie").json() == {"q": "custom", "limit": 10}
    assert client.get("/cookie", COOKIES={"q": "c"}).json() == {"q": "c", "limit": 10}
    assert client.post("/form").json() == {"q": "custom", "limit": 10}
    assert client.post("/form", POST={"q": "f"}).json() == {"q": "f", "limit": 10}


def test_schema_with_a_default_documents_none_of_its_fields_as_required():
    api = HattoriAPI()

    @api.get("/default")
    def default(request, f: Query[Filters] = Filters(q="custom")) -> None:
        pass

    @api.get("/factory")
    def factory(
        request, f: Filters = Query(default_factory=lambda: Filters(q="made"))
    ) -> None:
        pass

    @api.get("/nested")
    def nested(request, f: Query[Outer]) -> None:
        pass

    @api.post("/form")
    def form(request, f: Form[Filters] = Filters(q="custom")) -> None:
        pass

    @api.get("/by-name")
    def by_name(request, f: Query[ByName | None] = None) -> None:
        pass

    assert required_params(api, "/api/default") == {"q": False, "limit": False}
    assert required_params(api, "/api/factory") == {"q": False, "limit": False}
    assert required_params(api, "/api/nested") == {"x": False, "y": False}
    assert required_params(api, "/api/by-name") == {"q": False}
    form_body = api.get_openapi_schema()["paths"]["/api/form"]["post"]["requestBody"]
    schema = form_body["content"]["application/x-www-form-urlencoded"]["schema"]
    assert "required" not in schema


def test_schema_with_a_none_default_that_cannot_be_none_is_built_from_its_fields():
    api = HattoriAPI()

    @api.get("/none")
    def none(request, f: Filters = Query(None)) -> dict:
        return f.model_dump()

    @api.get("/annotated-none")
    def annotated_none(request, f: Query[Filters] = None) -> dict:
        return f.model_dump()

    client = TestClient(api)

    for path in ("/none", "/annotated-none"):
        assert client.get(path).status_code == 422
        assert client.get(f"{path}?q=x").json() == {"q": "x", "limit": 10}
        assert required_params(api, f"/api{path}") == {"q": True, "limit": False}


def test_schema_without_a_default_is_still_built_from_its_fields():
    api = HattoriAPI()

    @api.get("/required")
    def required(request, f: Query[Filters]) -> dict:
        return f.model_dump()

    client = TestClient(api)

    assert client.get("/required").status_code == 422
    assert client.get("/required?q=x").json() == {"q": "x", "limit": 10}
    assert required_params(api, "/api/required") == {"q": True, "limit": False}
