from typing import TYPE_CHECKING, Any, NoReturn

from django.http import Http404, HttpRequest, HttpResponse

from hattori.openapi.docs import DocsBase
from hattori.responses import JsonResponse

if TYPE_CHECKING:
    # if anyone knows a cleaner way to make mypy happy - welcome
    from hattori import HattoriAPI  # pragma: no cover


def default_home(request: HttpRequest, api: "HattoriAPI", **kwargs: Any) -> NoReturn:
    "This view is mainly needed to determine the full path for API operations"
    docs_url = f"{request.path}{api.docs_url}".replace("//", "/")
    raise Http404(f"docs_url = {docs_url}")


def openapi_json(request: HttpRequest, api: "HattoriAPI", **kwargs: Any) -> HttpResponse:
    schema = api.get_openapi_schema(path_params=kwargs)
    return JsonResponse(schema)


def openapi_view(request: HttpRequest, api: "HattoriAPI", **kwargs: Any) -> HttpResponse:
    docs: DocsBase = api.docs
    return docs.render_page(request, api, **kwargs)
