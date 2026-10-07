import logging

import pytest
from django.core.exceptions import (
    BadRequest,
    DisallowedHost,
    PermissionDenied,
    RequestDataTooBig,
    SuspiciousOperation,
    TooManyFieldsSent,
)
from django.core.handlers.exception import response_for_exception
from django.http import Http404
from django.http.multipartparser import MultiPartParserError
from django.test import RequestFactory

from hattori import Form, HattoriAPI, Schema
from hattori.errors import AuthorizationError, HttpError
from hattori.testing import TestAsyncClient, TestClient

api = HattoriAPI()


class CustomException(Exception):
    pass


@api.exception_handler(CustomException)
def on_custom_error(request, exc):
    return api.create_response(request, {"custom": True}, status=422)


class Payload(Schema):
    test: int


@api.post("/error/{code}")
def err_thrower(request, code: str, payload: Payload = None) -> None:
    if code == "base":
        raise RuntimeError("test")
    if code == "404":
        raise Http404("test")
    if code == "404-bare":
        raise Http404
    if code == "403":
        raise PermissionDenied("test")
    if code == "403-bare":
        raise PermissionDenied
    if code == "400":
        raise BadRequest("test")
    if code == "suspicious":
        raise SuspiciousOperation("test")
    if code == "host":
        raise DisallowedHost("test")
    if code == "custom":
        raise CustomException("test")
    return None


client = TestClient(api)


def test_default_handler(settings):
    settings.DEBUG = True

    response = client.post("/error/base")
    assert response.status_code == 500
    assert b"RuntimeError: test" in response.content

    response = client.post("/error/404")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found: test"}
    assert client.post("/error/404-bare").json() == {"detail": "Not Found"}

    response = client.post("/error/403")
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden: test"}
    assert client.post("/error/403-bare").json() == {"detail": "Forbidden"}

    response = client.post("/error/400")
    assert response.status_code == 400
    assert response.json() == {"detail": "Bad Request: test"}

    response = client.post("/error/custom", body="invalid_json")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail.startswith("Cannot parse request body (")

    settings.DEBUG = False
    with pytest.raises(RuntimeError):
        response = client.post("/error/base")

    response = client.post("/error/custom", body="invalid_json")
    assert response.status_code == 400
    assert response.json() == {"detail": "Cannot parse request body"}


@pytest.mark.parametrize(
    "route,status_code,json",
    [
        ("/error/404", 404, {"detail": "Not Found"}),
        ("/error/403", 403, {"detail": "Forbidden"}),
        ("/error/400", 400, {"detail": "Bad Request"}),
        ("/error/suspicious", 400, {"detail": "Bad Request"}),
        ("/error/host", 400, {"detail": "Bad Request"}),
        ("/error/custom", 422, {"custom": True}),
    ],
)
def test_exceptions(route, status_code, json):
    response = client.post(route)
    assert response.status_code == status_code
    assert response.json() == json


def _log_entries(caplog):
    return [
        (r.name, r.levelname, r.getMessage(), r.status_code, bool(r.exc_info))
        for r in caplog.records
        if r.name.startswith("django.")
    ]


@pytest.mark.parametrize(
    "exc",
    [
        Http404("gone"),
        PermissionDenied("staff only"),
        PermissionDenied(),
        BadRequest("unreadable"),
        MultiPartParserError("no boundary"),
        SuspiciousOperation("tampered"),
        DisallowedHost("evil.example"),
        RequestDataTooBig("too big"),
        TooManyFieldsSent("too many"),
    ],
    ids=lambda exc: type(exc).__name__,
)
def test_django_exceptions_keep_django_status_and_log_entries(caplog, exc):
    # Django's own handler is the reference for the status and the log entries.
    caplog.set_level(logging.DEBUG, logger="django")
    request = RequestFactory().get("/error")
    django_response = response_for_exception(request, exc)
    django_entries = _log_entries(caplog)
    caplog.clear()

    response = api.on_exception(RequestFactory().get("/error"), exc)

    assert response.status_code == django_response.status_code
    assert response["Content-Type"] == "application/json; charset=utf-8"
    assert _log_entries(caplog) == django_entries


def test_django_log_entry_survives_a_handler_that_fails(caplog):
    api = HattoriAPI()

    @api.exception_handler(HttpError)
    def broken(request, exc):
        raise RuntimeError("renderer failed")

    @api.get("/tampered")
    def tampered(request) -> None:
        raise SuspiciousOperation("tampered")

    caplog.set_level(logging.DEBUG, logger="django")
    with pytest.raises(RuntimeError, match="renderer failed"):
        TestClient(api).get("/tampered")

    assert _log_entries(caplog) == [
        ("django.security.SuspiciousOperation", "ERROR", "tampered", 400, True)
    ]


def test_over_limit_form_can_be_reported_without_raising_again(settings, caplog):
    # Django's error reporting reads request.POST, which is what raised.
    settings.DATA_UPLOAD_MAX_NUMBER_FIELDS = 2
    api = HattoriAPI()

    @api.post("/form")
    def form(request, name: Form[str]) -> str:
        return name

    seen = []

    class ReadsPost(logging.Handler):
        def emit(self, record):
            seen.append(dict(record.request.POST))

    logger = logging.getLogger("django.security.TooManyFieldsSent")
    handler = ReadsPost()
    logger.addHandler(handler)
    try:
        response = TestClient(api).post(
            "/form",
            body=b"a=1&b=2&c=3&name=x",
            content_type="application/x-www-form-urlencoded",
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 400
    assert response.json() == {"detail": "Bad Request"}
    assert seen == [{}]


def test_django_exception_is_the_cause_of_the_http_error():
    api = HattoriAPI()
    seen = []

    @api.exception_handler(HttpError)
    def record(request, exc):
        seen.append(exc)
        return api.create_response(request, {}, status=exc.status_code)

    @api.get("/missing")
    def missing(request) -> None:
        raise Http404("gone")

    TestClient(api).get("/missing")

    assert isinstance(seen[0].__cause__, Http404)


def test_permission_denied_is_an_authorization_error():
    api = HattoriAPI()

    @api.exception_handler(AuthorizationError)
    def forbidden(request, exc):
        return api.create_response(request, {"forbidden": True}, status=403)

    @api.get("/private")
    def private(request) -> None:
        raise PermissionDenied

    assert TestClient(api).get("/private").json() == {"forbidden": True}


def test_unreadable_form_data_is_a_bad_request():
    api = HattoriAPI()

    @api.post("/form")
    def form(request, name: Form[str]) -> str:
        return name

    response = TestClient(api).post(
        "/form", body=b"not multipart", content_type="multipart/form-data"
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Bad Request"}


def test_request_body_over_the_size_limit_is_a_bad_request(settings):
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 10

    response = client.post("/error/none", json={"test": 12345678901234567890})

    assert response.status_code == 400
    assert response.json() == {"detail": "Bad Request"}


@pytest.mark.asyncio
async def test_asyncio_exceptions():
    api = HattoriAPI()

    @api.get("/error")
    async def thrower(request) -> None:
        raise Http404("test")

    client = TestAsyncClient(api)
    response = await client.get("/error")
    assert response.status_code == 404


def test_no_handlers():
    api = HattoriAPI()
    api._exception_handlers = {}

    @api.get("/error")
    def thrower(request) -> None:
        raise RuntimeError("test")

    client = TestClient(api)

    with pytest.raises(RuntimeError):
        client.get("/error")
