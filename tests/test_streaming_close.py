"""A stream closes the iterator its view gave once the response is done with.

Each view keeps a reference to its iterator, as a view holding it elsewhere
would, so that it is never closed only because nothing refers to it anymore.
"""

import json

import pytest
from django.core.signals import request_finished
from django.db import close_old_connections
from pydantic import ValidationError

from hattori import JSONL, HattoriAPI, Schema
from hattori.testing import TestAsyncClient, TestClient


class Item(Schema):
    n: int


opened: list = []
events: list[str] = []


@pytest.fixture(autouse=True)
def _reset():
    opened.clear()
    events.clear()
    yield
    opened.clear()


class Closable:
    """An iterator, not a generator, holding something it must let go of."""

    def __init__(self, items):
        self._items = iter(items)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._items)

    def close(self):
        events.append("closed")


class AsyncClosable:
    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self):
        events.append("closed")


api = HattoriAPI()


@api.get("/iterator")
def iterator(request) -> JSONL[Item]:
    opened.append(source := Closable([{"n": 1}, {"n": 2}]))
    return source


def _items():
    try:
        yield {"n": 1}
        yield {"n": 2}
        yield {"n": "not a number"}
        yield {"n": 3}
    finally:
        events.append("closed")


@api.get("/generator")
def generator(request) -> JSONL[Item]:
    opened.append(source := _items())
    return source


async def _async_items():
    try:
        yield {"n": 1}
        yield {"n": 2}
        yield {"n": "not a number"}
        yield {"n": 3}
    finally:
        events.append("closed")


@api.get("/async-iterator")
async def async_iterator(request) -> JSONL[Item]:
    opened.append(source := AsyncClosable([{"n": 1}, {"n": 2}]))
    return source


@api.get("/async-generator")
async def async_generator(request) -> JSONL[Item]:
    opened.append(source := _async_items())
    return source


client = TestClient(api)
async_client = TestAsyncClient(api)


def _respond(path):
    """The view's response, unread, as a server is handed it."""
    func, request, kwargs = client._resolve("GET", path, {}, {})
    return func(request, **kwargs)


def _close(response):
    """Close the response as a server does once it is done with it.

    Without the request_finished handler that would reach for the database,
    as Django's own test client closes one.
    """
    request_finished.disconnect(close_old_connections)
    try:
        response.close()
    finally:
        request_finished.connect(close_old_connections)


async def _respond_async(path):
    func, request, kwargs = async_client._resolve("GET", path, {}, {})
    return await func(request, **kwargs)


class TestSync:
    def test_iterator_read_to_its_end_is_closed_with_the_response(self):
        response = _respond("/iterator")
        body = b"".join(response.streaming_content)
        assert [json.loads(line) for line in body.splitlines()] == [{"n": 1}, {"n": 2}]
        assert events == []

        _close(response)
        assert events == ["closed"]

    def test_generator_cut_short_is_closed_with_the_response(self):
        response = _respond("/generator")
        content = iter(response.streaming_content)
        assert json.loads(next(content)) == {"n": 1}

        _close(response)
        assert events == ["closed"]

    def test_stream_never_read_is_closed_with_the_response(self):
        response = _respond("/generator")
        _close(response)
        assert events == ["closed"]

    def test_generator_that_fails_mid_stream_is_closed_with_the_response(self):
        response = _respond("/generator")
        with pytest.raises(ValidationError):
            b"".join(response.streaming_content)

        _close(response)
        assert events == ["closed"]


@pytest.mark.asyncio
class TestAsync:
    async def test_iterator_read_to_its_end_is_closed(self):
        response = await async_client.get("/async-iterator")
        assert response.status_code == 200
        assert events == ["closed"]

    async def test_generator_that_fails_mid_stream_is_closed(self):
        response = await _respond_async("/async-generator")
        with pytest.raises(ValidationError):
            async for _ in response.streaming_content:
                pass
        assert events == ["closed"]

    async def test_generator_cut_short_is_closed_with_its_chunks(self):
        # What a server cut off leaves behind: the chunks, suspended, until
        # they are closed (by the event loop, once nothing holds them).
        response = await _respond_async("/async-generator")
        chunks = response._iterator
        assert json.loads(await anext(chunks)) == {"n": 1}
        assert events == []

        await chunks.aclose()
        assert events == ["closed"]
