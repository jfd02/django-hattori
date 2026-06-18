import json

import pytest
from django.http import HttpResponse

from hattori import JSONL, SSE, HattoriAPI, Schema
from hattori.streaming import StreamFormat
from hattori.testing import TestAsyncClient, TestClient


class Item(Schema):
    name: str
    price: float = 0.0


def _raw_sync(client, method, path):
    """Resolve and invoke an operation, returning the *unconsumed*
    StreamingHttpResponse — mirrors a real WSGI server which reads headers
    before iterating the body."""
    func, request, kwargs = client._resolve(method, path, {}, {})
    return func(request, **kwargs)


async def _raw_async(client, method, path):
    """Async counterpart of _raw_sync — returns the unconsumed response."""
    func, request, kwargs = client._resolve(method, path, {}, {})
    return await func(request, **kwargs)


def _drain_sync(raw):
    return b"".join(
        c.encode() if isinstance(c, str) else c for c in raw.streaming_content
    )


async def _drain_async(raw):
    chunks = []
    async for c in raw.streaming_content:
        chunks.append(c.encode() if isinstance(c, str) else c)
    return b"".join(chunks)


# --- Sync JSONL ---

api = HattoriAPI()


@api.get("/jsonl/items")
def jsonl_items(request) -> JSONL[Item]:
    for i in range(3):
        yield {"name": f"item-{i}", "price": float(i)}


@api.get("/sse/items")
def sse_items(request) -> SSE[Item]:
    for i in range(3):
        yield {"name": f"item-{i}", "price": float(i)}


@api.post("/jsonl/echo")
def jsonl_echo(request) -> JSONL[Item]:
    yield {"name": "posted", "price": 1.0}


@api.get("/jsonl/with-params/{item_id}")
def jsonl_with_params(request, item_id: int, q: str = "default") -> JSONL[Item]:
    yield {"name": f"item-{item_id}-{q}", "price": 0.0}


@api.get("/jsonl/with-headers")
def jsonl_with_headers(request, response: HttpResponse) -> JSONL[Item]:
    response["X-Custom"] = "hello"
    response.set_cookie("session", "abc123")
    yield {"name": "with-headers", "price": 0.0}


@api.get("/jsonl/multi-with-headers")
def jsonl_multi_with_headers(request, response: HttpResponse) -> JSONL[Item]:
    # Headers set before the first yield must reach the client even though
    # several more items follow.
    response["X-Custom"] = "hello"
    response.set_cookie("session", "abc123")
    for i in range(3):
        yield {"name": f"item-{i}", "price": float(i)}


@api.get("/jsonl/empty-with-headers")
def jsonl_empty_with_headers(request, response: HttpResponse) -> JSONL[Item]:
    response["X-Custom"] = "hello"
    response.set_cookie("session", "abc123")
    return
    yield  # pragma: no cover - makes this a generator function


@api.get("/jsonl/midstream-headers")
def jsonl_midstream_headers(request, response: HttpResponse) -> JSONL[Item]:
    response["X-Before"] = "before"
    yield {"name": "first", "price": 0.0}
    # Set after the first yield — the response is already flushed, so this
    # cannot reach the client. Documented limitation.
    response["X-After"] = "after"
    yield {"name": "second", "price": 1.0}


@api.get("/jsonl/status-before-yield")
def jsonl_status_before_yield(request, response: HttpResponse) -> JSONL[Item]:
    response.status_code = 503
    response["X-Reason"] = "degraded"
    yield {"name": "a", "price": 0.0}


client = TestClient(api)


class TestJSONLSync:
    def test_jsonl_basic(self):
        response = client.get("/jsonl/items")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/jsonl"
        lines = response.content.decode().strip().split("\n")
        assert len(lines) == 3
        for i, line in enumerate(lines):
            data = json.loads(line)
            assert data == {"name": f"item-{i}", "price": float(i)}

    def test_jsonl_validates_schema(self):
        """Each item is validated through Pydantic schema."""
        response = client.get("/jsonl/items")
        lines = response.content.decode().strip().split("\n")
        for line in lines:
            data = json.loads(line)
            # Should have both fields (price has default)
            assert "name" in data
            assert "price" in data


class TestSSESync:
    def test_sse_basic(self):
        response = client.get("/sse/items")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"
        content = response.content.decode()
        events = content.strip().split("\n\n")
        assert len(events) == 3
        for i, event in enumerate(events):
            assert event.startswith("data: ")
            data = json.loads(event[len("data: ") :])
            assert data == {"name": f"item-{i}", "price": float(i)}

    def test_sse_headers(self):
        response = client.get("/sse/items")
        assert response["Cache-Control"] == "no-cache"
        assert response["X-Accel-Buffering"] == "no"


class TestPostStreaming:
    def test_post_jsonl(self):
        response = client.post("/jsonl/echo")
        assert response.status_code == 200
        lines = response.content.decode().strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"name": "posted", "price": 1.0}


class TestStreamingWithParams:
    def test_path_and_query_params(self):
        response = client.get("/jsonl/with-params/42?q=test")
        assert response.status_code == 200
        lines = response.content.decode().strip().split("\n")
        assert json.loads(lines[0]) == {"name": "item-42-test", "price": 0.0}


class TestStreamingHeaders:
    def test_temporal_response_headers(self):
        response = client.get("/jsonl/with-headers")
        assert response.status_code == 200
        assert response["X-Custom"] == "hello"
        assert "session" in response.cookies

    def test_headers_available_before_body_consumed(self):
        """Production ordering: a WSGI server flushes the status line and
        headers BEFORE iterating the response body. Headers/cookies the view
        set before its first yield must already be present at that point."""
        raw = _raw_sync(client, "GET", "/jsonl/with-headers")
        # Read headers/cookies BEFORE touching the body (real-server order).
        assert raw["X-Custom"] == "hello"
        assert "session" in raw.cookies
        # Body still streams correctly afterwards.
        body = _drain_sync(raw)
        assert json.loads(body.decode().strip()) == {
            "name": "with-headers",
            "price": 0.0,
        }

    def test_headers_before_body_with_multiple_items(self):
        raw = _raw_sync(client, "GET", "/jsonl/multi-with-headers")
        assert raw["X-Custom"] == "hello"
        assert "session" in raw.cookies
        lines = _drain_sync(raw).decode().strip().split("\n")
        assert len(lines) == 3
        for i, line in enumerate(lines):
            assert json.loads(line) == {"name": f"item-{i}", "price": float(i)}

    def test_headers_before_body_empty_generator(self):
        """A view that sets headers then yields nothing must still surface
        those headers before the (empty) body is consumed."""
        raw = _raw_sync(client, "GET", "/jsonl/empty-with-headers")
        assert raw["X-Custom"] == "hello"
        assert "session" in raw.cookies
        assert _drain_sync(raw) == b""

    def test_headers_set_after_first_yield_are_not_honored(self):
        """Documented limitation: only headers set before the first yield can
        reach the client. Headers set mid-stream are silently dropped, not
        attempted at the end."""
        raw = _raw_sync(client, "GET", "/jsonl/midstream-headers")
        assert raw["X-Before"] == "before"
        assert "X-After" not in raw
        # Even after the whole body is consumed, the mid-stream header never
        # appears on the response.
        body = _drain_sync(raw)
        assert "X-After" not in raw
        assert len(body.decode().strip().split("\n")) == 2

    def test_status_code_set_before_first_yield(self):
        """A status code set before the first yield is reflected on the
        streamed response — the status line is flushed before the body."""
        raw = _raw_sync(client, "GET", "/jsonl/status-before-yield")
        assert raw.status_code == 503
        assert raw["X-Reason"] == "degraded"
        assert _drain_sync(raw)  # body still streams


# --- Async ---

async_api = HattoriAPI()


@async_api.get("/jsonl/items")
async def async_jsonl_items(request) -> JSONL[Item]:
    for i in range(3):
        yield {"name": f"item-{i}", "price": float(i)}


@async_api.get("/sse/items")
async def async_sse_items(request) -> SSE[Item]:
    for i in range(3):
        yield {"name": f"item-{i}", "price": float(i)}


@async_api.get("/jsonl/with-headers")
async def async_jsonl_with_headers(request, response: HttpResponse) -> JSONL[Item]:
    response["X-Custom"] = "async-hello"
    response.set_cookie("token", "xyz")
    yield {"name": "async-headers", "price": 0.0}


@async_api.get("/jsonl/multi-with-headers")
async def async_jsonl_multi_with_headers(
    request, response: HttpResponse
) -> JSONL[Item]:
    response["X-Custom"] = "async-hello"
    response.set_cookie("token", "xyz")
    for i in range(3):
        yield {"name": f"item-{i}", "price": float(i)}


@async_api.get("/jsonl/empty-with-headers")
async def async_jsonl_empty_with_headers(
    request, response: HttpResponse
) -> JSONL[Item]:
    response["X-Custom"] = "async-hello"
    response.set_cookie("token", "xyz")
    return
    yield  # pragma: no cover - makes this an async generator function


@async_api.get("/jsonl/status-before-yield")
async def async_jsonl_status_before_yield(
    request, response: HttpResponse
) -> JSONL[Item]:
    response.status_code = 503
    response["X-Reason"] = "degraded"
    yield {"name": "a", "price": 0.0}


async_client = TestAsyncClient(async_api)


@pytest.mark.asyncio
class TestAsyncJSONL:
    async def test_async_jsonl(self):
        response = await async_client.get("/jsonl/items")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/jsonl"
        lines = response.content.decode().strip().split("\n")
        assert len(lines) == 3
        for i, line in enumerate(lines):
            data = json.loads(line)
            assert data == {"name": f"item-{i}", "price": float(i)}


@pytest.mark.asyncio
class TestAsyncSSE:
    async def test_async_sse(self):
        response = await async_client.get("/sse/items")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"
        assert response["Cache-Control"] == "no-cache"
        content = response.content.decode()
        events = content.strip().split("\n\n")
        assert len(events) == 3


@pytest.mark.asyncio
class TestAsyncHeaders:
    async def test_async_temporal_response_headers(self):
        response = await async_client.get("/jsonl/with-headers")
        assert response.status_code == 200
        assert response["X-Custom"] == "async-hello"
        assert "token" in response.cookies

    async def test_async_headers_available_before_body_consumed(self):
        """Async/ASGI counterpart: headers set before the first yield must be
        present before the body is iterated."""
        raw = await _raw_async(async_client, "GET", "/jsonl/with-headers")
        assert raw["X-Custom"] == "async-hello"
        assert "token" in raw.cookies
        body = await _drain_async(raw)
        assert json.loads(body.decode().strip()) == {
            "name": "async-headers",
            "price": 0.0,
        }

    async def test_async_headers_before_body_with_multiple_items(self):
        raw = await _raw_async(async_client, "GET", "/jsonl/multi-with-headers")
        assert raw["X-Custom"] == "async-hello"
        assert "token" in raw.cookies
        lines = (await _drain_async(raw)).decode().strip().split("\n")
        assert len(lines) == 3
        for i, line in enumerate(lines):
            assert json.loads(line) == {"name": f"item-{i}", "price": float(i)}

    async def test_async_headers_before_body_empty_generator(self):
        raw = await _raw_async(async_client, "GET", "/jsonl/empty-with-headers")
        assert raw["X-Custom"] == "async-hello"
        assert "token" in raw.cookies
        assert await _drain_async(raw) == b""

    async def test_async_status_code_set_before_first_yield(self):
        raw = await _raw_async(async_client, "GET", "/jsonl/status-before-yield")
        assert raw.status_code == 503
        assert raw["X-Reason"] == "degraded"
        assert await _drain_async(raw)


# --- OpenAPI Schema ---


class TestOpenAPISchema:
    def test_jsonl_openapi(self):
        schema = api.get_openapi_schema()
        path = schema["paths"]["/api/jsonl/items"]["get"]
        resp = path["responses"][200]
        assert "application/jsonl" in resp["content"]
        item_schema = resp["content"]["application/jsonl"]["schema"]
        # Should reference the Item schema
        assert item_schema.get("$ref") or item_schema.get("properties")

    def test_sse_openapi(self):
        schema = api.get_openapi_schema()
        path = schema["paths"]["/api/sse/items"]["get"]
        resp = path["responses"][200]
        assert "text/event-stream" in resp["content"]
        sse_schema = resp["content"]["text/event-stream"]["schema"]
        assert sse_schema["type"] == "object"
        assert "data" in sse_schema["properties"]


# --- Custom StreamFormat ---


class NDJSON(StreamFormat):
    media_type = "application/x-ndjson"

    @classmethod
    def format_chunk(cls, data: str) -> str:
        return data + "\n"


custom_api = HattoriAPI()


@custom_api.get("/ndjson/items")
def ndjson_items(request) -> NDJSON[Item]:
    for i in range(2):
        yield {"name": f"item-{i}", "price": float(i)}


custom_client = TestClient(custom_api)


class TestCustomFormat:
    def test_custom_ndjson(self):
        response = custom_client.get("/ndjson/items")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/x-ndjson"
        lines = response.content.decode().strip().split("\n")
        assert len(lines) == 2

    def test_custom_openapi(self):
        schema = custom_api.get_openapi_schema()
        path = schema["paths"]["/api/ndjson/items"]["get"]
        resp = path["responses"][200]
        assert "application/x-ndjson" in resp["content"]


# --- Multiple methods ---

multi_api = HattoriAPI()


@multi_api.patch("/patch-stream")
def patch_stream(request) -> JSONL[Item]:
    yield {"name": "patched", "price": 0.0}


@multi_api.put("/put-stream")
def put_stream(request) -> JSONL[Item]:
    yield {"name": "put", "price": 0.0}


@multi_api.delete("/delete-stream")
def delete_stream(request) -> JSONL[Item]:
    yield {"name": "deleted", "price": 0.0}


multi_client = TestClient(multi_api)


class TestMultipleMethods:
    def test_patch_stream(self):
        response = multi_client.patch("/patch-stream")
        assert response.status_code == 200
        assert json.loads(response.content.decode().strip()) == {
            "name": "patched",
            "price": 0.0,
        }

    def test_put_stream(self):
        response = multi_client.put("/put-stream")
        assert response.status_code == 200
        assert json.loads(response.content.decode().strip()) == {
            "name": "put",
            "price": 0.0,
        }

    def test_delete_stream(self):
        response = multi_client.delete("/delete-stream")
        assert response.status_code == 200
        assert json.loads(response.content.decode().strip()) == {
            "name": "deleted",
            "price": 0.0,
        }


# --- Exceptions raised before the first yield ---
#
# Because the generator is primed up to its first yield while the request is
# still being handled, an error raised there is dispatched through the API's
# exception handling (on_exception) — producing a proper, non-streaming error
# response — instead of corrupting an already-flushed stream. (An error raised
# *mid-stream*, after the first yield, cannot be turned into an error response:
# the status line and headers have already gone to the client.)

exc_api = HattoriAPI()
exc_api.add_exception_handler(
    ValueError, lambda request, exc: HttpResponse(str(exc), status=400)
)


@exc_api.get("/raise-before-yield")
def exc_raise_before_yield(request) -> JSONL[Item]:
    raise ValueError("boom before first yield")
    yield  # pragma: no cover - makes this a generator function


@exc_api.get("/raise-before-yield-async")
async def exc_raise_before_yield_async(request) -> JSONL[Item]:
    raise ValueError("boom before first yield")
    yield  # pragma: no cover - makes this an async generator function


exc_client = TestClient(exc_api)
exc_async_client = TestAsyncClient(exc_api)


class TestStreamExceptionBeforeFirstYield:
    def test_handled_by_exception_handler(self):
        response = exc_client.get("/raise-before-yield")
        assert response.status_code == 400
        assert response.streaming is False
        assert response.content == b"boom before first yield"

    @pytest.mark.asyncio
    async def test_handled_by_exception_handler_async(self):
        response = await exc_async_client.get("/raise-before-yield-async")
        assert response.status_code == 400
        assert response.streaming is False
        assert response.content == b"boom before first yield"
