"""The HttpError body is declared once and drives both the response and the spec.

An operation with a request body documents a 400, because a body that cannot be
parsed raises ``HttpError(400)``. Before ``set_http_error_model`` that schema
was fixed at ``{"detail": str}``, so a project that replaced the ``HttpError``
handler published a 400 body it never sent.
"""

from http import HTTPStatus
from typing import Self

import pytest
from django.core.exceptions import BadRequest, PermissionDenied, SuspiciousOperation
from django.http import Http404
from openapi_contract import export_contract, validate_response

from hattori import ApiError, HattoriAPI, HttpErrorBody, Schema
from hattori.errors import (
    ConfigError,
    HttpError,
    HttpErrorResponse,
    get_http_error_model,
    set_http_error_model,
)
from hattori.testing import TestClient


class Item(Schema):
    name: str


class Problem(HttpErrorBody):
    """Nothing like the shipped ``{detail: str}``."""

    code: str
    message: str

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        status = HTTPStatus(error.status_code)
        return cls(code=status.name.lower(), message=str(error))


class NameTaken(ApiError):
    code = 400
    error_code = "name_taken"
    message = "That name is taken"


def _api() -> HattoriAPI:
    api = HattoriAPI()

    @api.post("/items")
    def create_item(request, item: Item) -> Item:
        return item

    @api.post("/unique-items")
    def create_unique_item(request, item: Item) -> Item | NameTaken:
        return item

    @api.get("/teapot")
    def teapot(request) -> Item:
        raise HttpError(418, "Short and stout")

    @api.get("/items")
    def list_items(request) -> list[Item]:
        return []

    @api.get("/missing")
    def missing(request) -> Item:
        raise Http404("No such item")

    @api.get("/private")
    def private(request) -> Item:
        raise PermissionDenied("Staff only")

    @api.get("/malformed")
    def malformed(request) -> Item:
        raise BadRequest("Unreadable")

    @api.get("/suspicious")
    def suspicious(request) -> Item:
        raise SuspiciousOperation("Tampered")

    return api


@pytest.fixture
def problem_model():
    original = get_http_error_model()
    set_http_error_model(Problem)
    yield
    set_http_error_model(original)


def test_default_body_is_detail():
    assert get_http_error_model() is HttpErrorResponse
    api = _api()
    document = export_contract(api)

    response = TestClient(api).post(
        "/items", body=b"{", content_type="application/json"
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Cannot parse request body"}
    validate_response(document, "/api/items", response, method="post")
    assert document["paths"]["/api/items"]["post"]["responses"]["400"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/HttpErrorResponse"}


def test_custom_model_shapes_the_response_and_the_spec(problem_model):
    api = _api()
    document = export_contract(api)

    response = TestClient(api).post(
        "/items", body=b"{", content_type="application/json"
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": "bad_request",
        "message": "Cannot parse request body",
    }
    validate_response(document, "/api/items", response, method="post")
    schemas = document["components"]["schemas"]
    assert set(schemas["Problem"]["properties"]) == {"code", "message"}
    assert "HttpErrorResponse" not in schemas


def test_custom_model_is_documented_beside_a_declared_400(problem_model):
    api = _api()
    document = export_contract(api)

    schema = document["paths"]["/api/unique-items"]["post"]["responses"]["400"][
        "content"
    ]["application/json"]["schema"]

    assert schema == {
        "anyOf": [
            {"$ref": "#/components/schemas/NameTaken"},
            {"$ref": "#/components/schemas/Problem"},
        ]
    }
    malformed = TestClient(api).post(
        "/unique-items", body=b"{", content_type="application/json"
    )
    validate_response(document, "/api/unique-items", malformed, method="post")


def test_every_http_error_uses_the_model(problem_model):
    response = TestClient(_api()).get("/teapot")

    assert response.status_code == 418
    assert response.json() == {"code": "im_a_teapot", "message": "Short and stout"}


@pytest.mark.parametrize(
    "method,path,status,body",
    [
        ("GET", "/missing", 404, {"code": "not_found", "message": "Not Found"}),
        ("GET", "/private", 403, {"code": "forbidden", "message": "Forbidden"}),
        ("GET", "/malformed", 400, {"code": "bad_request", "message": "Bad Request"}),
        ("GET", "/suspicious", 400, {"code": "bad_request", "message": "Bad Request"}),
        ("GET", "/", 404, {"code": "not_found", "message": "Not Found"}),
        (
            "PUT",
            "/teapot",
            405,
            {"code": "method_not_allowed", "message": "Method Not Allowed"},
        ),
    ],
)
def test_errors_the_framework_answers_use_the_model(
    problem_model, settings, method, path, status, body
):
    settings.DEBUG = False

    response = TestClient(_api()).request(method, path)

    assert response.status_code == status
    assert response.json() == body


def test_errors_the_framework_answers_reach_an_http_error_handler():
    api = _api()

    @api.exception_handler(HttpError)
    def envelope(request, exc):
        return api.create_response(
            request, {"error": exc.status_code}, status=exc.status_code
        )

    client = TestClient(api)

    assert client.get("/missing").json() == {"error": 404}
    assert client.get("/private").json() == {"error": 403}
    assert client.get("/malformed").json() == {"error": 400}
    assert client.get("/").json() == {"error": 404}
    assert client.put("/teapot").json() == {"error": 405}
    assert client.put("/teapot")["Allow"] == "GET, HEAD, OPTIONS"


def test_operations_without_a_body_document_no_400(problem_model):
    document = export_contract(_api())

    assert "400" not in document["paths"]["/api/items"]["get"]["responses"]


def test_a_user_model_of_the_same_name_is_not_clobbered(problem_model):
    api = HattoriAPI()

    class Problem(Schema):  # the user's own, unrelated model
        severity: int

    @api.post("/problems")
    def report(request, problem: Problem) -> Problem:
        return problem

    schemas = export_contract(api)["components"]["schemas"]

    assert set(schemas["Problem"]["properties"]) == {"severity"}
    assert set(schemas["Problem_2"]["properties"]) == {"code", "message"}


def test_model_must_subclass_http_error_body():
    class NotABody(Schema):
        detail: str

    with pytest.raises(ConfigError, match="must subclass hattori.HttpErrorBody"):
        set_http_error_model(NotABody)  # type: ignore[arg-type]
    with pytest.raises(ConfigError):
        set_http_error_model("Problem")  # type: ignore[arg-type]
    assert get_http_error_model() is HttpErrorResponse


def test_base_model_requires_from_error():
    with pytest.raises(NotImplementedError):
        HttpErrorBody.from_error(HttpError(400, "x"))
