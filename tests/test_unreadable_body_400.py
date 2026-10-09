"""An operation documents the 400 it answers a body it cannot read with.

JSON is decoded by the operation itself, whatever the method. Form and
multipart data is Django's to parse, and Django parses them for ``POST`` only,
inside the operation, where a failure is answered through the ``HttpError``
model. The spec used to list that 400 for JSON bodies alone.

Django's own test ``Client`` is used throughout: hattori's ``TestClient`` hands
the view a prepared request, and would hide where the parsing happens.
"""

import pytest
from django.test import Client, override_settings
from django.urls import path
from openapi_contract import export_contract, validate_response

from hattori import File, Form, HattoriAPI, Query, Schema, UploadedFile
from hattori.compatibility.files import FIX_MIDDLEWARE_PATH

NO_BOUNDARY = "multipart/form-data"
URLENCODED = "application/x-www-form-urlencoded"


class Item(Schema):
    name: str


api = HattoriAPI(urls_namespace="unreadable-body")

# PUT with a file needs the files middleware installed when it is registered.
with override_settings(MIDDLEWARE=[FIX_MIDDLEWARE_PATH]):

    @api.post("/form")
    def post_form(request, text: str = Form("default")) -> str:
        return text

    @api.post("/upload")
    def post_upload(request, file: UploadedFile = File(...)) -> int:
        return file.size

    @api.put("/form")
    def put_form(request, text: str = Form("default")) -> str:
        return text

    @api.put("/upload")
    def put_upload(request, file: UploadedFile = File(...)) -> int:
        return file.size

    @api.put("/json")
    def put_json(request, item: Item) -> Item:
        return item

    @api.post("/query")
    def post_query(request, text: str = Query("default")) -> str:
        return text

    @api.api_operation(["POST", "PUT"], "/either")
    def either(request, text: str = Form("default")) -> str:
        return text


urlpatterns = [path("api/", api.urls)]


@pytest.fixture
def document(settings):
    settings.ROOT_URLCONF = __name__
    settings.MIDDLEWARE = []
    settings.ALLOWED_HOSTS = ["testserver"]
    return export_contract(api)


def _responses(document, path, method):
    return document["paths"][f"/api{path}"][method]["responses"]


def _assert_documented_400(document, path, method, response):
    assert response.status_code == 400
    assert response.json() == {"detail": "Bad Request"}
    validate_response(document, f"/api{path}", response, method=method)


@pytest.mark.parametrize("path", ["/form", "/upload", "/either"])
def test_post_answers_unreadable_multipart_with_the_documented_400(document, path):
    response = Client().post(f"/api{path}", data=b"x", content_type=NO_BOUNDARY)

    _assert_documented_400(document, path, "post", response)


def test_post_answers_a_form_past_the_upload_limits_with_the_documented_400(
    document, settings
):
    settings.DATA_UPLOAD_MAX_NUMBER_FIELDS = 2
    too_many = Client().post("/api/form", data=b"a=1&b=2&c=3", content_type=URLENCODED)
    settings.DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 8
    too_big = Client().post(
        "/api/form", data=b"text=" + b"x" * 64, content_type=URLENCODED
    )

    _assert_documented_400(document, "/form", "post", too_many)
    _assert_documented_400(document, "/form", "post", too_big)


def test_json_body_documents_its_400_whatever_the_method(document):
    response = Client().put("/api/json", data=b"{", content_type="application/json")

    assert response.status_code == 400
    assert response.json() == {"detail": "Cannot parse request body"}
    validate_response(document, "/api/json", response, method="put")


@pytest.mark.parametrize("path", ["/form", "/either"])
def test_form_on_another_method_without_the_files_middleware_documents_no_400(
    document, path
):
    # Django reads form data for POST only: the operation sees none at all.
    response = Client().put(f"/api{path}", data=b"x", content_type=NO_BOUNDARY)

    assert response.status_code == 200
    assert response.json() == "default"
    assert "400" not in _responses(document, path, "put")


def test_upload_on_another_method_is_refused_before_the_api_is_reached(
    document, settings
):
    # The files middleware parses the body on the way in. What it cannot read
    # is answered by Django, not by the API, so it is not the API's documented 400.
    settings.MIDDLEWARE = [FIX_MIDDLEWARE_PATH]

    response = Client().put("/api/upload", data=b"x", content_type=NO_BOUNDARY)

    assert response.status_code == 400
    assert response["Content-Type"].startswith("text/html")
    assert "400" not in _responses(document, "/upload", "put")


def test_operation_that_reads_no_body_documents_no_400(document):
    response = Client().post("/api/query", data=b"x", content_type=NO_BOUNDARY)

    assert response.status_code == 200
    assert set(_responses(document, "/query", "post")) == {"200"}
