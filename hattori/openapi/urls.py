from functools import partial
from typing import TYPE_CHECKING, Any

from django.urls import path, re_path

from .views import catch_all, default_home, openapi_json, openapi_view

if TYPE_CHECKING:
    from hattori import HattoriAPI  # pragma: no cover

__all__ = ["get_openapi_urls", "get_root_url", "get_catch_all_url"]


def get_openapi_urls(api: HattoriAPI) -> list[Any]:
    result = []

    if api.openapi_url:
        view = partial(openapi_json, api=api)
        if api.docs_decorator:
            view = api.docs_decorator(view)  # type: ignore
        result.append(
            path(api.openapi_url.lstrip("/"), view, name="openapi-json"),
        )

        assert api.openapi_url != api.docs_url, (
            "Please use different urls for openapi_url and docs_url"
        )

        if api.docs_url:
            view = partial(openapi_view, api=api)
            if api.docs_decorator:
                view = api.docs_decorator(view)  # type: ignore
            result.append(
                path(api.docs_url.lstrip("/"), view, name="openapi-view"),
            )

    return result


def _exempt(view: Any) -> Any:
    # Like every operation, these views run no user code, they only answer for
    # the API, so they are CSRF exempt too: a POST gets the API's 404, not
    # Django's CSRF page. Set on the partial itself rather than through the
    # decorator, because the root view is how the API is found again
    # (``resolve(root).func.keywords``).
    view.csrf_exempt = True
    return view


def get_root_url(api: HattoriAPI) -> Any:
    view = _exempt(partial(default_home, api=api))
    return path("", view, name="api-root")


def get_catch_all_url(api: HattoriAPI, routes: list[Any]) -> Any:
    view = _exempt(partial(catch_all, api=api, routes=routes))
    # [\s\S] rather than "." because a path can hold a newline, which "." stops
    # at, and rather than (?s) because reverse() cannot read an inline flag.
    return re_path(r"(?P<unmatched>[\s\S]+)", view)
