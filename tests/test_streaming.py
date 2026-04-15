import json

import pytest
from django.http import HttpResponse

from hattori import JSONL, SSE, HattoriAPI, Schema
from hattori.streaming import StreamFormat
from hattori.testing import TestAsyncClient, TestClient


class Item(Schema):
    name: str
    price: float = 0.0


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
def jsonl_with_params(
    request, item_id: int, q: str = "default"
) -> JSONL[Item]:
    yield {"name": f"item-{item_id}-{q}", "price": 0.0}


@api.get("/jsonl/with-headers")
def jsonl_with_headers(
    request, response: HttpResponse
) -> JSONL[Item]:
    response["X-Custom"] = "hello"
    response.set_cookie("session", "abc123")
    yield {"name": "with-headers", "price": 0.0}


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
async def async_jsonl_with_headers(
    request, response: HttpResponse
) -> JSONL[Item]:
    response["X-Custom"] = "async-hello"
    response.set_cookie("token", "xyz")
    yield {"name": "async-headers", "price": 0.0}


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
