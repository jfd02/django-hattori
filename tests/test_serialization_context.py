from pydantic import field_serializer, model_serializer

from hattori import JSONL, HattoriAPI, Router, Schema
from hattori.renderers import BaseRenderer
from hattori.testing import TestClient


def test_request_is_passed_in_context_when_supported():
    class SchemaWithCustomSerializer(Schema):
        test1: str
        test2: str

        @model_serializer(mode="wrap")
        def ser_model(self, handler, info):
            assert "request" in info.context
            assert info.context["request"].path == "/test"  # check it is HttRequest
            assert "response_status" in info.context

            return handler(self)

    def api_endpoint_test(
        request,
    ) -> SchemaWithCustomSerializer:
        return {
            "test1": "foo",
            "test2": "bar",
        }

    router = Router()
    router.add_api_operation("/test", ["GET"], api_endpoint_test)

    TestClient(router).get("/test")


def test_custom_renderers_keep_python_values():
    class Payload(Schema):
        value: int
        values: set[int]

        @field_serializer("value", when_used="json")
        def as_text(self, value) -> str:
            return str(value)

    class PythonRenderer(BaseRenderer):
        media_type = "text/plain"

        def render(self, request, data, *, response_status):
            assert data == {"value": 0, "values": {0}}
            return "ok"

    api = HattoriAPI(renderer=PythonRenderer())

    @api.get("/payload")
    def payload(request) -> Payload:
        return Payload(value=0, values={0})

    assert TestClient(api).get("/payload").content == b"ok"


def test_streamed_items_use_json_serialization():
    class Payload(Schema):
        value: int
        values: set[int]

        @field_serializer("value", when_used="json")
        def as_text(self, value) -> str:
            return str(value)

    api = HattoriAPI()

    @api.get("/payload")
    def payload(request) -> JSONL[Payload]:
        yield Payload(value=0, values={0})

    assert TestClient(api).get("/payload").content == b'{"value":"0","values":[0]}\n'
