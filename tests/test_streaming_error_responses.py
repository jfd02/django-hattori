"""On a streaming endpoint, only the streamed success body uses the stream media
type. Error responses (auth/permission short-circuits, extra declared errors) are
returned as regular JSON at runtime, so the OpenAPI spec must document them with
the renderer's media type — not ``application/jsonl`` / ``text/event-stream``.
"""

from collections.abc import Iterator

from hattori import JSONL, APIReturn, HattoriAPI, Schema
from hattori.renderers import BaseRenderer
from hattori.security import HttpBearer
from hattori.testing import TestClient


class Item(Schema):
    name: str


class AuthErr(Schema):
    reason: str


class BadToken(APIReturn[AuthErr]):
    code = 401


class Bearer(HttpBearer):
    def authenticate(self, request, token) -> object | BadToken:
        return {"u": 1}


def test_streaming_auth_errors_use_the_renderers_serialization_mode():
    class ErrorPayload(Schema):
        values: set[int]

    class Rejected(APIReturn[ErrorPayload]):
        code = 401

    class Auth(HttpBearer):
        def authenticate(self, request, token) -> object | Rejected:
            return object() if token == "ok" else Rejected(ErrorPayload(values={1}))

    class PythonRenderer(BaseRenderer):
        media_type = "text/plain"

        def render(self, request, data, *, response_status):
            assert response_status == 401
            assert data == {"values": {1}}
            return "Rejected"

    api = HattoriAPI(renderer=PythonRenderer())

    @api.get("/stream", auth=Auth())
    def stream(request) -> JSONL[set[int]]:
        yield {1}

    client = TestClient(api)
    rejected = client.get("/stream", headers={"Authorization": "Bearer bad"})
    assert rejected.status_code == 401
    assert rejected.content == b"Rejected"
    success = client.get("/stream", headers={"Authorization": "Bearer ok"})
    assert success.status_code == 200
    assert success.content == b"[1]\n"


def test_streaming_error_response_is_json_not_stream():
    api = HattoriAPI()

    @api.get("/stream", auth=Bearer())
    def stream(request) -> JSONL[Item]:  # noqa: ARG001
        def gen() -> Iterator[Item]:
            yield Item(name="a")

        return gen()

    responses = api.get_openapi_schema()["paths"]["/api/stream"]["get"]["responses"]

    # Success body streams as JSONL.
    assert list(responses[200]["content"].keys()) == ["application/jsonl"]

    # The auth 401 is a plain JSON response, not a JSONL stream item.
    assert list(responses[401]["content"].keys()) == ["application/json"]
    assert responses[401]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/AuthErr"
    )
