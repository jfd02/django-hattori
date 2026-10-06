from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.middleware.common import CommonMiddleware
from django.urls import Resolver404

from hattori.openapi.docs import DocsBase
from hattori.responses import JsonResponse

if TYPE_CHECKING:
    # if anyone knows a cleaner way to make mypy happy - welcome
    from hattori import HattoriAPI  # pragma: no cover

# Never run as middleware: it only builds the APPEND_SLASH redirect in catch_all.
_common = CommonMiddleware(lambda request: HttpResponse())


def default_home(request: HttpRequest, api: HattoriAPI, **kwargs: Any) -> HttpResponse:
    "This view is mainly needed to determine the full path for API operations"
    hint = ""
    if settings.DEBUG:
        docs_url = f"{request.path}{api.docs_url}".replace("//", "/")
        hint = f"docs_url = {docs_url}"
    return api.on_exception(request, Http404(hint))


def catch_all(
    request: HttpRequest,
    api: HattoriAPI,
    routes: list[Any],
    unmatched: str,
    **kwargs: Any,
) -> HttpResponse:
    "Answers a path under the API that no route matches: HattoriAPI(catch_all=True)"
    # Because this view matches it, the path is no longer a miss, and a miss is
    # what CommonMiddleware answers with its APPEND_SLASH redirect. So that
    # redirect is sent from here, when the API does route the path with a slash
    # added. CommonMiddleware still builds it, so the target is escaped, and in
    # DEBUG refused for a request with a body, exactly as Django's own is.
    if settings.APPEND_SLASH and not unmatched.endswith("/"):
        if _is_routed(routes, f"{unmatched}/"):
            target = _common.get_full_path_with_slash(request)
            return _common.response_redirect_class(target)
    return api.on_exception(request, Http404())


def _is_routed(routes: list[Any], path: str) -> bool:
    for route in routes:
        try:
            if route.resolve(path):
                return True
        except Resolver404:
            continue
    return False


def openapi_json(request: HttpRequest, api: HattoriAPI, **kwargs: Any) -> HttpResponse:
    schema = api.get_openapi_schema(path_params=kwargs)
    return JsonResponse(schema)


def openapi_view(request: HttpRequest, api: HattoriAPI, **kwargs: Any) -> HttpResponse:
    docs: DocsBase = api.docs
    return docs.render_page(request, api, **kwargs)
