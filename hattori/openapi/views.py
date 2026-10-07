from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NoReturn

from django.http import Http404, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase

from hattori.constants import NOT_SET
from hattori.openapi.docs import DocsBase
from hattori.operation import Operation
from hattori.responses import JsonResponse

if TYPE_CHECKING:
    # if anyone knows a cleaner way to make mypy happy - welcome
    from hattori import HattoriAPI  # pragma: no cover


def default_home(request: HttpRequest, api: HattoriAPI, **kwargs: Any) -> NoReturn:
    "This view is mainly needed to determine the full path for API operations"
    docs_url = f"{request.path}{api.docs_url}".replace("//", "/")
    raise Http404(f"docs_url = {docs_url}")


def openapi_json(request: HttpRequest, api: HattoriAPI, **kwargs: Any) -> HttpResponse:
    schema = api.get_openapi_schema(path_params=kwargs)
    return JsonResponse(schema)


def openapi_view(request: HttpRequest, api: HattoriAPI, **kwargs: Any) -> HttpResponse:
    docs: DocsBase = api.docs
    return docs.render_page(request, api, **kwargs)


def _docs(request: HttpRequest) -> None:
    pass  # pragma: no cover


def guard_docs(
    api: HattoriAPI, view: Callable[..., HttpResponse]
) -> Callable[..., HttpResponseBase]:
    """Put a docs view behind the checks the API's own operations run.

    Unless ``docs_auth`` says otherwise, the docs are no more public than the
    API they describe: they take the API-wide ``auth`` and ``permissions``.
    """
    if api.docs_auth is NOT_SET:
        auth, permissions = api.auth, api.permissions
    else:
        auth, permissions = api.docs_auth, None
    # Never routed to: an operation is what knows how to run these checks and
    # how to answer for them, typed auth responses and exception handlers
    # included.
    guard = Operation("", ["GET"], _docs, auth=auth, permissions=permissions)
    if not (guard.auth_callbacks or guard.permission_callbacks):
        return view
    guard.api = api

    def guarded(request: HttpRequest, **kwargs: Any) -> HttpResponseBase:
        denied = guard._run_checks(
            request, api.create_temporal_response(request), kwargs
        )
        if denied is not None:
            return denied
        return view(request, **kwargs)

    return guarded
