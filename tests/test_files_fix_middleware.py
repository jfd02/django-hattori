"""Integration tests for ``fix_request_files_middleware``.

Django only populates ``request.FILES`` for POST requests
(https://code.djangoproject.com/ticket/12635); hattori ships a middleware that
re-parses the body for PUT/PATCH/DELETE so file params work there too. The unit
``TestClient`` calls views directly and bypasses middleware, so this behaviour
can only be exercised end-to-end through Django's real test ``Client`` against a
urlconf that installs the middleware — which is what these tests do.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import AsyncClient, Client, override_settings
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from django.urls import path

from hattori import File, HattoriAPI, Schema, UploadedFile
from hattori.compatibility.files import FIX_MIDDLEWARE_PATH

# The middleware stack the real Client runs through. The fix middleware must be
# present both when the endpoints are *registered* (the @api.patch/.put
# decorators raise ConfigError otherwise) and when requests are *served*.
MIDDLEWARE = [FIX_MIDDLEWARE_PATH]


class FileEcho(Schema):
    name: str
    content: str


class Msg(Schema):
    text: str


sync_api = HattoriAPI(urls_namespace="files-fix-sync")
async_api = HattoriAPI(urls_namespace="files-fix-async")


def _read(file: UploadedFile) -> FileEcho:
    return FileEcho(name=file.name or "", content=file.read().decode())


# Endpoints are registered at import time, so the middleware has to be installed
# in settings *now* for need_to_fix_request_files() to accept PUT/PATCH + File.
with override_settings(MIDDLEWARE=MIDDLEWARE):

    @sync_api.patch("/upload")
    def patch_upload(request, file: UploadedFile = File(...)) -> FileEcho:
        return _read(file)

    @sync_api.put("/upload")
    def put_upload(request, file: UploadedFile = File(...)) -> FileEcho:
        return _read(file)

    @sync_api.patch("/json")
    def patch_json(request, payload: Msg) -> Msg:
        # No file param: exercises the middleware's "skip non-file body" branch.
        return payload

    @async_api.patch("/upload")
    async def async_patch_upload(request, file: UploadedFile = File(...)) -> FileEcho:
        return _read(file)

    @async_api.get("/ping")
    async def async_ping(request) -> str:
        # GET isn't a fix-method, so the async middleware passes it straight
        # through without touching the body.
        return "pong"


urlpatterns = [
    path("sync/", sync_api.urls),
    path("async/", async_api.urls),
]


def _multipart(**files: SimpleUploadedFile) -> str:
    return encode_multipart(BOUNDARY, files)


@pytest.fixture
def _wired(settings):
    settings.ROOT_URLCONF = __name__
    settings.MIDDLEWARE = MIDDLEWARE
    settings.ALLOWED_HOSTS = ["testserver"]


@pytest.mark.parametrize("method", ["patch", "put"])
def test_sync_file_upload_populated_by_middleware(_wired, method):
    body = _multipart(file=SimpleUploadedFile("a.txt", b"data123"))
    resp = getattr(Client(), method)(
        "/sync/upload", data=body, content_type=MULTIPART_CONTENT
    )
    assert resp.status_code == 200, resp.content
    assert resp.json() == {"name": "a.txt", "content": "data123"}


def test_sync_json_body_skips_file_reparsing(_wired):
    # content_type is application/json, so _should_fix() is False and the
    # middleware leaves the request untouched.
    resp = Client().patch(
        "/sync/json", data={"text": "hi"}, content_type="application/json"
    )
    assert resp.status_code == 200, resp.content
    assert resp.json() == {"text": "hi"}


@pytest.mark.asyncio
async def test_async_file_upload_populated_by_middleware(_wired):
    body = _multipart(file=SimpleUploadedFile("b.txt", b"async-data"))
    resp = await AsyncClient().patch(
        "/async/upload", data=body, content_type=MULTIPART_CONTENT
    )
    assert resp.status_code == 200, resp.content
    assert resp.json() == {"name": "b.txt", "content": "async-data"}


@pytest.mark.asyncio
async def test_async_non_fix_method_passes_through(_wired):
    resp = await AsyncClient().get("/async/ping")
    assert resp.status_code == 200, resp.content
    assert resp.json() == "pong"
