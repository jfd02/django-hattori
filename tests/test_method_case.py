import pytest
from django.test import override_settings

from hattori import Form, HattoriAPI, Router
from hattori.errors import ConfigError
from hattori.testing import TestAsyncClient, TestClient


def test_method_declared_in_lowercase_is_answered():
    api = HattoriAPI()

    @api.api_operation(["get", "post"], "/thing")
    def thing(request) -> str:
        return "ok"

    client = TestClient(api)
    assert client.get("/thing").json() == "ok"
    assert client.post("/thing").json() == "ok"
    assert client.request("HEAD", "/thing").status_code == 200

    refused = client.put("/thing")
    assert refused.status_code == 405
    assert refused["Allow"] == "GET, POST, HEAD, OPTIONS"


@pytest.mark.asyncio
async def test_method_declared_in_lowercase_is_answered_by_an_async_view():
    api = HattoriAPI()

    @api.api_operation(["get"], "/thing")
    async def thing(request) -> str:
        return "ok"

    response = await TestAsyncClient(api).get("/thing")
    assert response.status_code == 200
    assert response.json() == "ok"


def test_method_declared_in_lowercase_on_a_router_is_answered():
    api = HattoriAPI()
    router = Router()

    @router.api_operation(["patch"], "/thing")
    def thing(request) -> str:
        return "ok"

    api.add_router("/r", router)
    assert TestClient(api).patch("/r/thing").json() == "ok"


def test_method_is_documented_once_whatever_case_it_is_declared_in():
    api = HattoriAPI()

    @api.api_operation(["get", "GET"], "/thing")
    def thing(request) -> str:
        return "ok"

    operations = api.get_openapi_schema()["paths"]["/api/thing"]
    assert list(operations) == ["get"]
    assert not operations["get"]["operationId"].endswith("_get")


def test_method_is_a_duplicate_whatever_case_it_is_declared_in():
    api = HattoriAPI()

    @api.get("/thing")
    def first(request) -> str:
        return "first"

    with pytest.raises(ConfigError, match=r"Duplicate method\(s\) \['GET'\]"):
        api.api_operation(["get"], "/thing")(first)


def test_form_on_a_lowercase_method_is_refused_without_the_files_middleware():
    api = HattoriAPI()

    def update(request, name: str = Form(...)) -> str:
        return name

    with override_settings(MIDDLEWARE=[]):
        with pytest.raises(ConfigError, match="read a form or a file"):
            api.api_operation(["put"], "/thing")(update)
