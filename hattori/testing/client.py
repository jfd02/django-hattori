import asyncio
import inspect
from collections.abc import Callable
from typing import Any, ClassVar
from urllib.parse import urljoin

from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.core.handlers.base import BaseHandler
from django.http import HttpRequest, QueryDict, StreamingHttpResponse
from django.test import RequestFactory

from hattori import HattoriAPI, Router
from hattori.responses import JsonResponse as HttpResponse
from hattori.responses import json_dumps, json_loads


def build_absolute_uri(location: str | None = None) -> str:
    base = "http://testlocation/"

    if location:
        base = urljoin(base, location)

    return base


# TODO: this should be changed
# maybe add here urlconf object and add urls from here
class HattoriClientBase:
    __test__ = False  # <- skip pytest

    def __init__(
        self,
        router_or_app: HattoriAPI | Router,
        headers: dict[str, str] | None = None,
        COOKIES: dict[str, str] | None = None,
    ) -> None:
        self.headers = headers or {}
        self.cookies = COOKIES or {}
        self.router_or_app = router_or_app
        self._factory = RequestFactory()

    def _prepare_request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        json: Any = None,
        **request_params: Any,
    ) -> tuple[Callable, HttpRequest, dict]:
        if json is not None:
            request_params["body"] = json_dumps(json)
            # What ``json=`` sends is JSON; an explicit content_type still wins.
            request_params.setdefault("content_type", "application/json")
        if data is None:
            data = {}
        if self.headers or request_params.get("headers"):
            request_params["headers"] = {
                **self.headers,
                **request_params.get("headers", {}),
            }
        if self.cookies or request_params.get("COOKIES"):
            request_params["COOKIES"] = {
                **self.cookies,
                **request_params.get("COOKIES", {}),
            }
        return self._resolve(method, path, data, request_params)

    @property
    def urls(self) -> list:
        if not hasattr(self, "_urls_cache"):
            self._urls_cache: list
            if isinstance(self.router_or_app, HattoriAPI):
                self._urls_cache = self.router_or_app.urls[0]
            else:
                # Create temporary API without mutating router
                # Unique namespace prevents registry conflicts
                api = HattoriAPI(urls_namespace=f"test-{id(self)}")
                api.add_router("", self.router_or_app)
                self._urls_cache = api.urls[0]
        return self._urls_cache

    def _resolve(
        self, method: str, path: str, data: dict, request_params: Any
    ) -> tuple[Callable, HttpRequest, dict]:
        url_path = path.split("?")[0].lstrip("/")
        for url in self.urls:
            match = url.resolve(url_path)
            if match:
                request = self._build_request(method, path, data, request_params)
                request.resolver_match = match
                # Django's handler is bypassed here, so open the ATOMIC_REQUESTS
                # transaction it would have run the view in.
                func = BaseHandler().make_view_atomic(match.func)
                return func, request, match.kwargs
        raise Exception(f'Cannot resolve "{path}"')

    def _build_request(
        self, method: str, path: str, data: dict, request_params: Any
    ) -> HttpRequest:
        query_data = None
        if method.upper() in {"GET", "HEAD"} and isinstance(data, dict):
            # As in Django's test client, what a GET is given is its query.
            query_data, data = data, {}
        post, body = self._resolve_payload(data, request_params)
        full_path = self._fold_query_params(
            path, query_data, request_params.pop("query_params", None)
        )
        request = self._make_request(method, full_path, body, request_params)
        self._apply_request_attrs(request, post, request_params)
        return request

    @staticmethod
    def _to_querydict(values: dict) -> QueryDict:
        """Build a QueryDict from a plain dict, expanding list values into the
        repeated-key form (``{"k": [1, 2]}`` -> ``k=1&k=2``)."""
        if isinstance(values, QueryDict):
            return values
        qd = QueryDict(mutable=True)
        for key, value in values.items():
            if isinstance(value, list):
                for item in value:
                    qd.appendlist(key, item)
            else:
                qd[key] = value
        return qd

    def _resolve_payload(self, data: dict, request_params: Any) -> tuple[Any, Any]:
        """Map the form payload (``data``) to a (request.POST, body) pair.

        An explicit ``POST=`` or ``body=`` kwarg wins over ``data``; a string or
        bytes ``data`` becomes the raw body, a dict becomes request.POST.
        """
        body = request_params.pop("body", None)
        post = request_params.pop("POST", None)
        if post is None:
            if isinstance(data, QueryDict):
                post = data
            elif isinstance(data, (str, bytes)):
                if body is None:
                    body = data
            elif data:
                post = self._to_querydict(data)
        return post, body

    def _fold_query_params(self, path: str, *sources: dict | None) -> str:
        """Return ``path`` with each of ``sources`` folded into its query
        string, so the real request parses GET for us.

        A later source replaces the keys it shares with an earlier one, the
        path's own query being the earliest, and leaves the rest alone.
        """
        given = [values for values in sources if values]
        if not given:
            return path
        url_path, _, query_string = path.partition("?")
        merged = QueryDict(query_string, mutable=True)
        for values in given:
            for key, items in self._to_querydict(values).lists():
                merged.setlist(key, items)
        return f"{url_path}?{merged.urlencode()}"

    def _make_request(
        self, method: str, full_path: str, body: Any, request_params: Any
    ) -> HttpRequest:
        """Build a real request via Django's RequestFactory."""
        factory_kwargs: dict[str, Any] = {}
        headers = request_params.pop("headers", {})
        if headers:
            factory_kwargs["headers"] = headers
        request = self._factory.generic(
            method,
            full_path,
            data=body if body is not None else b"",
            content_type=request_params.pop("content_type", None)
            or "application/octet-stream",
            **factory_kwargs,
        )
        # RequestFactory seeds an empty Cookie header; drop it so request.headers
        # reflects only what the caller sent. Cookies are delivered to handlers
        # through request.COOKIES (set below), not the Cookie header.
        request.META.pop("HTTP_COOKIE", None)
        extra_meta = request_params.pop("META", None)
        if extra_meta:
            request.META.update(extra_meta)
        return request

    def _apply_request_attrs(
        self, request: HttpRequest, post: Any, request_params: Any
    ) -> None:
        """Attach hattori conveniences and any leftover kwargs to the request."""
        request.COOKIES = request_params.pop("COOKIES", {})
        request.auth = None  # type: ignore[attr-defined]
        request.user = request_params.pop("user", None) or AnonymousUser()
        request.session = {}  # type: ignore[assignment]

        async def auser() -> Any:
            return request.user

        request.auser = auser
        request._dont_enforce_csrf_checks = True  # type: ignore[attr-defined]
        request.build_absolute_uri = build_absolute_uri  # type: ignore[method-assign]

        if post is not None:
            request.POST = post
        files = request_params.pop("FILES", None)
        if files is not None:
            request._files = files  # type: ignore[attr-defined]

        for key, value in request_params.items():
            setattr(request, key, value)


class TestClient(HattoriClientBase):
    def get(
        self, path: str, data: dict | None = None, **request_params: Any
    ) -> HattoriTestResponse:
        return self.request("GET", path, data, **request_params)

    def post(
        self,
        path: str,
        data: dict | None = None,
        json: Any = None,
        **request_params: Any,
    ) -> HattoriTestResponse:
        return self.request("POST", path, data, json, **request_params)

    def patch(
        self,
        path: str,
        data: dict | None = None,
        json: Any = None,
        **request_params: Any,
    ) -> HattoriTestResponse:
        return self.request("PATCH", path, data, json, **request_params)

    def put(
        self,
        path: str,
        data: dict | None = None,
        json: Any = None,
        **request_params: Any,
    ) -> HattoriTestResponse:
        return self.request("PUT", path, data, json, **request_params)

    def delete(
        self,
        path: str,
        data: dict | None = None,
        json: Any = None,
        **request_params: Any,
    ) -> HattoriTestResponse:
        return self.request("DELETE", path, data, json, **request_params)

    def request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        json: Any = None,
        **request_params: Any,
    ) -> HattoriTestResponse:
        func, request, kwargs = self._prepare_request(
            method, path, data, json, **request_params
        )
        return self._call(func, request, kwargs)

    def _call(
        self, func: Callable, request: HttpRequest, kwargs: dict
    ) -> HattoriTestResponse:
        response = func(request, **kwargs)
        if inspect.iscoroutine(response):
            # An async view: run it to its end, as Django does under WSGI.
            response = self._finish(response)
        return HattoriTestResponse(response)

    @staticmethod
    def _finish(pending: Any) -> HttpResponse | StreamingHttpResponse:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            finished: HttpResponse | StreamingHttpResponse = async_to_sync(
                _finish_async_response
            )(pending)
            return finished
        pending.close()
        raise RuntimeError(
            "TestClient reached an async view from inside a running event loop. "
            "Use TestAsyncClient there and await the request."
        )


class TestAsyncClient(HattoriClientBase):
    async def get(
        self, path: str, data: dict | None = None, **request_params: Any
    ) -> HattoriTestResponse:
        return await self.request("GET", path, data, **request_params)

    async def post(
        self,
        path: str,
        data: dict | None = None,
        json: Any = None,
        **request_params: Any,
    ) -> HattoriTestResponse:
        return await self.request("POST", path, data, json, **request_params)

    async def patch(
        self,
        path: str,
        data: dict | None = None,
        json: Any = None,
        **request_params: Any,
    ) -> HattoriTestResponse:
        return await self.request("PATCH", path, data, json, **request_params)

    async def put(
        self,
        path: str,
        data: dict | None = None,
        json: Any = None,
        **request_params: Any,
    ) -> HattoriTestResponse:
        return await self.request("PUT", path, data, json, **request_params)

    async def delete(
        self,
        path: str,
        data: dict | None = None,
        json: Any = None,
        **request_params: Any,
    ) -> HattoriTestResponse:
        return await self.request("DELETE", path, data, json, **request_params)

    async def request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        json: Any = None,
        **request_params: Any,
    ) -> HattoriTestResponse:
        func, request, kwargs = self._prepare_request(
            method, path, data, json, **request_params
        )
        return await self._call(func, request, kwargs)

    async def _call(
        self, func: Callable, request: HttpRequest, kwargs: dict
    ) -> HattoriTestResponse:
        if inspect.iscoroutinefunction(func):
            return HattoriTestResponse(
                await _finish_async_response(func(request, **kwargs))
            )

        # A sync view: off the event loop, as Django runs it under ASGI. Its
        # response is read there too, since a sync stream is still sync code.
        def call() -> HattoriTestResponse:
            return HattoriTestResponse(func(request, **kwargs))

        return await sync_to_async(call, thread_sensitive=True)()


async def _finish_async_response(pending: Any) -> HttpResponse | StreamingHttpResponse:
    """Await an async view's response, reading an async stream while its loop runs."""
    http_response = await pending
    if http_response.streaming and inspect.isasyncgen(http_response.streaming_content):
        # Async streaming: consume async iterator into bytes
        chunks = []
        async for chunk in http_response.streaming_content:
            chunks.append(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
        # Replace with sync content for HattoriTestResponse
        http_response.streaming_content = iter(chunks)
    finished: HttpResponse | StreamingHttpResponse = http_response
    return finished


class HattoriTestResponse:
    _UNSET: ClassVar[object] = object()

    def __init__(self, http_response: HttpResponse | StreamingHttpResponse):
        self._response = http_response
        self.status_code = http_response.status_code
        self.streaming = http_response.streaming
        if self.streaming:
            assert isinstance(http_response, StreamingHttpResponse)
            self.content = b"".join(
                chunk.encode("utf-8") if isinstance(chunk, str) else chunk
                for chunk in http_response.streaming_content  # type: ignore[union-attr]
            )
        else:
            self.content = http_response.content
        self._data: Any = self._UNSET

    def json(self) -> Any:
        return json_loads(self.content)

    @property
    def data(self) -> Any:
        # Cache via a sentinel so a body that decodes to ``null`` (-> None) is
        # parsed once, not on every access.
        if self._data is self._UNSET:
            self._data = self.json()
        return self._data

    def __getitem__(self, key: str) -> Any:
        return self._response[key]

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._response, attr)
