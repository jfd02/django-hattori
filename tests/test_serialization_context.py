from typing import Any

import pytest
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy
from pydantic import field_serializer, model_serializer

from hattori import JSONL, HattoriAPI, Router, Schema
from hattori.renderers import BaseRenderer
from hattori.testing import TestClient

RESPONSE_SHAPES = ["instance", "dict", "list", "optional"]


def _respond(model: type[Schema], values: dict[str, Any], shape: str) -> Any:
    """What an endpoint sends for ``values`` returned as ``shape`` of ``model``."""
    annotation, value = {
        "instance": (model, model(**values)),
        "dict": (model, values),
        "list": (list[model], [model(**values)]),
        "optional": (model | None, model(**values)),
    }[shape]
    api = HattoriAPI()

    @api.get("/payload")
    def payload(request) -> annotation:
        return value

    response = TestClient(api).get("/payload")
    assert response.status_code == 200
    body = response.json()
    return body[0] if shape == "list" else body


@pytest.mark.parametrize("shape", RESPONSE_SHAPES)
def test_model_serializer_shapes_every_response(shape):
    class Payload(Schema):
        public: str
        secret: str

        @model_serializer(mode="wrap")
        def redact(self, handler, info):
            assert info.context["request"].path == "/payload"
            data = handler(self)
            del data["secret"]
            return data

    values = {"public": "hello", "secret": "hidden"}
    assert _respond(Payload, values, shape) == {"public": "hello"}


@pytest.mark.parametrize("shape", RESPONSE_SHAPES)
def test_model_dump_override_is_never_called(shape):
    # A response is dumped by the declared type's pydantic serializer, the same
    # way for every shape. model_dump is not a hook into that.
    class Payload(Schema):
        public: str

        def model_dump(self, **kwargs):
            raise AssertionError("model_dump must not be called")

    assert _respond(Payload, {"public": "hello"}, shape) == {"public": "hello"}


@pytest.mark.parametrize("as_model", [False, True])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("legacy_pydantic", [False, True])
def test_lazy_translations_in_json_responses(
    as_model, stream, legacy_pydantic, monkeypatch
):
    if legacy_pydantic:
        monkeypatch.setattr(
            "hattori.operation.pydantic_version", [2, 10], raising=False
        )

    class Payload(Schema):
        message: Any

    payload = {"message": [gettext_lazy("Hello")]}
    value = Payload(**payload) if as_model else payload
    annotation = Payload if as_model else dict
    api = HattoriAPI()

    if stream:

        @api.get("/lazy")
        def lazy(request) -> JSONL[annotation]:
            yield value

    else:

        @api.get("/lazy")
        def lazy(request) -> annotation:
            return value

    response = TestClient(api).get("/lazy")
    assert response.status_code == 200
    assert response.content == b'{"message":["Hello"]}' + (b"\n" if stream else b"")
    assert isinstance(payload["message"][0], Promise)


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
        message: Any

        @field_serializer("value", when_used="json")
        def as_text(self, value) -> str:
            return str(value)

    class PythonRenderer(BaseRenderer):
        media_type = "text/plain"

        def render(self, request, data, *, response_status):
            assert data["value"] == 0
            assert data["values"] == {0}
            assert isinstance(data["message"], Promise)
            return "ok"

    api = HattoriAPI(renderer=PythonRenderer())

    @api.get("/payload")
    def payload(request) -> Payload:
        return Payload(value=0, values={0}, message=gettext_lazy("Hello"))

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
