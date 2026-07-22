"""On a streaming endpoint, only the streamed success body uses the stream media
type. Error responses (auth/permission short-circuits, extra declared errors) are
returned as regular JSON at runtime, so the OpenAPI spec must document them with
the renderer's media type — not ``application/jsonl`` / ``text/event-stream``.
"""

from collections.abc import Iterator

from hattori import JSONL, APIReturn, HattoriAPI, Schema
from hattori.security import HttpBearer


class Item(Schema):
    name: str


class AuthErr(Schema):
    reason: str


class BadToken(APIReturn[AuthErr]):
    code = 401


class Bearer(HttpBearer):
    def authenticate(self, request, token) -> object | BadToken:
        return {"u": 1}


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
