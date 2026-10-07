from collections.abc import Iterator
from functools import partial
from typing import TYPE_CHECKING, Any

from django.core.checks import CheckMessage, Error
from django.urls import URLPattern, URLResolver, get_resolver, path
from django.urls.resolvers import RoutePattern

from .views import default_home, guard_docs, openapi_json, openapi_view

if TYPE_CHECKING:
    from hattori import HattoriAPI  # pragma: no cover

__all__ = ["get_openapi_urls", "get_root_url", "shared_namespace_error"]


def get_openapi_urls(api: HattoriAPI) -> list[Any]:
    result = []

    if api.openapi_url:
        view = guard_docs(api, partial(openapi_json, api=api))
        if api.docs_decorator:
            view = api.docs_decorator(view)  # type: ignore
        result.append(
            path(api.openapi_url.lstrip("/"), view, name="openapi-json"),
        )

        assert api.openapi_url != api.docs_url, (
            "Please use different urls for openapi_url and docs_url"
        )

        if api.docs_url:
            view = guard_docs(api, partial(openapi_view, api=api))
            if api.docs_decorator:
                view = api.docs_decorator(view)  # type: ignore
            result.append(
                path(api.docs_url.lstrip("/"), view, name="openapi-view"),
            )

    return result


class APIRoot(URLPattern):
    """An API's root url, which also marks where the API is mounted."""

    def __init__(self, api: HattoriAPI) -> None:
        super().__init__(
            RoutePattern("", name="api-root", is_endpoint=True),
            partial(default_home, api=api),
            name="api-root",
        )
        self.api = api

    def check(self) -> list[CheckMessage]:
        # Django checks every pattern of the URLconf, so this runs whether or
        # not hattori is an installed app.
        messages = super().check()
        mounts = _sharing_namespace(self.api, get_resolver())
        # The first mount speaks for all of them, to report the clash once.
        if mounts and mounts[0][1] is self:
            messages.append(
                Error(_shared_namespace_message(self.api, mounts), id="hattori.E001")
            )
        return messages


def get_root_url(api: HattoriAPI) -> APIRoot:
    return APIRoot(api)


def shared_namespace_error(api: HattoriAPI, resolver: URLResolver) -> str | None:
    """Why the API can't find its own urls, if another API shares its namespace.

    Django reverses a namespace to the first place it is mounted, so an API
    that shares one would be handed another API's urls.
    """
    mounts = _sharing_namespace(api, resolver)
    return _shared_namespace_message(api, mounts) if mounts else None


def _sharing_namespace(
    api: HattoriAPI, resolver: URLResolver
) -> list[tuple[str, APIRoot]]:
    # One API mounted twice is left alone: both mounts describe the same API.
    mounts = [
        (route, root)
        for namespace, route, root in _api_mounts(resolver)
        if namespace == api.urls_namespace
    ]
    return mounts if len({id(root.api) for _, root in mounts}) > 1 else []


def _api_mounts(
    resolver: URLResolver, namespace: str = "", route: str = ""
) -> Iterator[tuple[str, str, APIRoot]]:
    for pattern in resolver.url_patterns:
        if isinstance(pattern, APIRoot):
            yield namespace, route, pattern
        elif isinstance(pattern, URLResolver):
            names = filter(None, (namespace, pattern.namespace))
            yield from _api_mounts(
                pattern, ":".join(names), route + str(pattern.pattern)
            )


def _shared_namespace_message(
    api: HattoriAPI, mounts: list[tuple[str, APIRoot]]
) -> str:
    routes = " and ".join(f'"{route}"' for route, _ in mounts)
    return (
        f"The APIs mounted at {routes} share the URL namespace "
        f'"{api.urls_namespace}". Django reverses it to the first of them only, '
        "so the others would be documented with its urls. "
        "Give each API its own urls_namespace= (or version=)."
    )
