from types import GenericAlias
from typing import Any, Generic, TypeVar

from hattori.responses import json_dumps

__all__ = ["StreamFormat", "SSE", "JSONL"]

_T = TypeVar("_T")


def _serialize_item(item: Any) -> str:
    return json_dumps(item).decode()


class StreamFormat(Generic[_T]):
    """Base class for streaming formats. Extensible by users.

    Anything affecting the response status line or headers — status code,
    headers, cookies set on the injected ``response`` object — must be done
    *before the first ``yield``*. The status line and headers are flushed to
    the client before the body is streamed, so changes made after the first
    ``yield`` cannot be sent and are ignored. For the same reason, an exception
    raised after the first ``yield`` cannot be converted into an error
    response; only one raised before it is dispatched through the API's
    exception handling.
    """

    media_type: str

    def __class_getitem__(cls, item_type: Any) -> GenericAlias:
        # ``JSONL[Item]``, for any format: one that does not say it is generic
        # is subscripted all the same. ``typing`` subscripts with a tuple when
        # it rebuilds the alias.
        args = item_type if isinstance(item_type, tuple) else (item_type,)
        if len(args) != 1:
            raise TypeError(f"{cls.__name__}[...] takes one item type.")
        return GenericAlias(cls, args)

    @classmethod
    def format_chunk(cls, data: str) -> str:
        """Format a serialized JSON string for this stream."""
        raise NotImplementedError  # pragma: no cover

    @classmethod
    def openapi_content_schema(cls, item_schema: dict) -> dict:
        """Generate OpenAPI content dict for this format."""
        return {cls.media_type: {"schema": item_schema}}

    @classmethod
    def response_headers(cls) -> dict[str, str]:
        """Extra headers for the streaming response."""
        return {}


class JSONL(StreamFormat, Generic[_T]):
    media_type = "application/jsonl"

    @classmethod
    def format_chunk(cls, data: str) -> str:
        return data + "\n"


class SSE(StreamFormat, Generic[_T]):
    media_type = "text/event-stream"

    @classmethod
    def format_chunk(cls, data: str) -> str:
        return f"data: {data}\n\n"

    @classmethod
    def response_headers(cls) -> dict[str, str]:
        return {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

    @classmethod
    def openapi_content_schema(cls, item_schema: dict) -> dict:
        return {
            cls.media_type: {
                "schema": {
                    "type": "object",
                    "properties": {
                        "data": item_schema,
                    },
                    "description": "SSE event with JSON data payload",
                }
            }
        }
