import json
import re

import pytest
from django.conf import settings
from django.http import StreamingHttpResponse
from django.test import override_settings

from hattori import ApiError, BasePermission, HattoriAPI, NoContent, Redoc, Swagger
from hattori.errors import AuthenticationError, ConfigError
from hattori.security import APIKeyQuery
from hattori.testing import TestClient

NO_HATTORI_INSTALLED_APPS = [i for i in settings.INSTALLED_APPS if i != "hattori"]


def test_swagger():
    "Default engine is swagger"
    api = HattoriAPI()

    assert isinstance(api.docs, Swagger)

    client = TestClient(api)

    response = client.get("/docs")
    assert response.status_code == 200
    assert b"swagger-ui-init.js" in response.content

    # Testing without hattori in INSTALLED_APPS
    @override_settings(INSTALLED_APPS=NO_HATTORI_INSTALLED_APPS)
    def call_docs():
        response = client.get("/docs")
        assert response.status_code == 200
        assert b"https://cdn.jsdelivr.net/npm/swagger-ui-dist" in response.content

    call_docs()


def test_swagger_settings():
    api = HattoriAPI(docs=Swagger(settings={"persistAuthorization": True}))
    client = TestClient(api)
    response = client.get("/docs")
    assert response.status_code == 200
    assert b'"persistAuthorization": true' in response.content


def test_redoc():
    api = HattoriAPI(docs=Redoc())
    client = TestClient(api)

    response = client.get("/docs")
    assert response.status_code == 200
    assert b"redoc.standalone.js" in response.content

    # Testing without hattori in INSTALLED_APPS
    @override_settings(INSTALLED_APPS=NO_HATTORI_INSTALLED_APPS)
    def call_docs():
        response = client.get("/docs")
        assert response.status_code == 200
        assert (
            b"https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js"
            in response.content
        )

    call_docs()


def test_redoc_settings():
    api = HattoriAPI(docs=Redoc(settings={"disableSearch": True}))
    client = TestClient(api)
    response = client.get("/docs")
    assert response.status_code == 200
    assert b'"disableSearch": true' in response.content


# --- Who can see the docs ---

DOCS_URLS = ("/docs", "/openapi.json")


class Key(APIKeyQuery):
    def authenticate(self, request, key):
        return key or None


class DocsKey(APIKeyQuery):
    param_name = "docs_key"

    def authenticate(self, request, key):
        return key or None


class StaffOnly(BasePermission):
    def check(self, request) -> bool:
        return request.auth == "staff"


def test_docs_take_the_api_auth():
    client = TestClient(HattoriAPI(auth=Key()))
    for url in DOCS_URLS:
        response = client.get(url)
        assert response.status_code == 401
        assert response.json() == {"detail": "Unauthorized"}
        assert client.get(f"{url}?key=k").status_code == 200


@pytest.mark.parametrize("docs", [Swagger(), Redoc()])
@pytest.mark.parametrize("installed_apps", [None, NO_HATTORI_INSTALLED_APPS])
def test_docs_page_passes_its_query_on_to_the_schema(docs, installed_apps):
    client = TestClient(HattoriAPI(auth=Key(), docs=docs))
    overrides = {"INSTALLED_APPS": installed_apps} if installed_apps else {}
    with override_settings(**overrides):
        page = client.get("/docs?key=k&theme=dark").content.decode()

    if isinstance(docs, Swagger):
        settings_json = re.search(
            r'id="swagger-settings">(.*?)</script>', page, re.DOTALL
        )
        url = json.loads(settings_json.group(1))["url"]
    else:
        # A JavaScript string literal, in which ``&`` is written as an escape.
        url = json.loads(f'"{re.search(r"Redoc.init\('(.*?)'", page).group(1)}"')

    assert url == "/api/openapi.json?key=k&theme=dark"
    assert client.get("/openapi.json").status_code == 401
    assert client.get(url.removeprefix("/api")).status_code == 200


@pytest.mark.parametrize("docs", [Swagger(), Redoc()])
def test_query_passed_on_cannot_break_out_of_the_page(docs):
    client = TestClient(HattoriAPI(auth=Key(), docs=docs))
    payload = "</script><script>alert('x')</script>"
    page = client.get("/docs", query_params={"key": payload}).content.decode()
    assert "alert(" not in page
    assert "%3C%2Fscript%3E" in page


def test_docs_take_the_api_permissions():
    client = TestClient(HattoriAPI(auth=Key(), permissions=[StaffOnly()]))
    for url in DOCS_URLS:
        assert client.get(f"{url}?key=guest").status_code == 403
        assert client.get(f"{url}?key=staff").status_code == 200


class NoVerdict(BasePermission):
    def check(self, request):
        return 0


def test_rejected_check_result_on_the_docs_is_answered_by_the_api_handlers():
    api = HattoriAPI(permissions=[NoVerdict()])
    answered = []

    @api.exception_handler(ConfigError)
    def answer(request, exc):
        answered.append(request.path)
        return api.create_response(request, {"detail": str(exc)}, status=503)

    client = TestClient(api)
    for url in DOCS_URLS:
        response = client.get(url)
        assert response.status_code == 503
        assert "NoVerdict.check returned int" in response.json()["detail"]

    assert len(answered) == len(DOCS_URLS)


def test_rejected_check_result_on_the_docs_no_handler_answers_is_raised_once():
    api = HattoriAPI(permissions=[NoVerdict()])
    offered = []

    @api.exception_handler(ConfigError)
    def hand_back(request, exc):
        offered.append(exc)
        raise exc

    client = TestClient(api)
    for url in DOCS_URLS:
        with pytest.raises(ConfigError, match="NoVerdict.check returned int"):
            client.get(url)

    assert len(offered) == len(DOCS_URLS)


def test_exception_from_a_docs_check_no_handler_answers_is_offered_once():
    def backend_down(request):
        raise RuntimeError("auth backend down")

    api = HattoriAPI(auth=backend_down)
    offered = []

    @api.exception_handler(RuntimeError)
    def hand_back(request, exc):
        offered.append(exc)
        raise exc

    client = TestClient(api)
    for url in DOCS_URLS:
        with pytest.raises(RuntimeError, match="auth backend down"):
            client.get(url)

    assert len(offered) == len(DOCS_URLS)


def _refused_by_a_handler(chunks):
    api = HattoriAPI(auth=Key())

    @api.exception_handler(AuthenticationError)
    def streamed(request, exc):
        return StreamingHttpResponse(chunks(), status=401)

    return api


def _rejected_and_answered_by_a_handler(chunks):
    api = HattoriAPI(permissions=[NoVerdict()])

    @api.exception_handler(ConfigError)
    def streamed(request, exc):
        return StreamingHttpResponse(chunks(), status=401)

    return api


def _refused_by_the_auth_itself(chunks):
    def streams(request):
        return StreamingHttpResponse(chunks(), status=401)

    return HattoriAPI(auth=streams)


@pytest.mark.parametrize(
    "refusing",
    [
        _refused_by_a_handler,
        _rejected_and_answered_by_a_handler,
        _refused_by_the_auth_itself,
    ],
)
def test_head_does_not_pull_a_stream_the_docs_are_refused_with(refusing):
    pulled = []

    def chunks():
        pulled.append(True)
        yield b"chunk"

    client = TestClient(refusing(chunks))
    for url in DOCS_URLS:
        response = client.request("HEAD", url)
        assert response.status_code == 401
        assert response.content == b""

    assert pulled == []
    # A request that does want the body still gets it.
    for url in DOCS_URLS:
        assert client.get(url).content == b"chunk"


def test_docs_auth_none_makes_the_docs_public():
    client = TestClient(HattoriAPI(auth=Key(), docs_auth=None))
    for url in DOCS_URLS:
        assert client.get(url).status_code == 200


def test_docs_auth_stands_in_for_the_api_checks():
    api = HattoriAPI(auth=Key(), permissions=[StaffOnly()], docs_auth=DocsKey())
    client = TestClient(api)
    for url in DOCS_URLS:
        assert client.get(f"{url}?key=staff").status_code == 401
        # Its own auth is all that guards the docs: the API's permissions are
        # written against what the API's auth returns.
        assert client.get(f"{url}?docs_key=anyone").status_code == 200


def test_docs_of_an_api_without_auth_can_still_be_guarded():
    client = TestClient(HattoriAPI(docs_auth=DocsKey()))
    for url in DOCS_URLS:
        assert client.get(url).status_code == 401
        assert client.get(f"{url}?docs_key=k").status_code == 200


def test_docs_answer_with_the_typed_response_of_the_auth():
    class BadKey(ApiError):
        code = 401
        error_code = "bad_key"
        message = "Unknown key"

    class TypedKey(APIKeyQuery):
        def authenticate(self, request, key) -> str | BadKey:
            return key or BadKey()

    client = TestClient(HattoriAPI(auth=TypedKey()))
    for url in DOCS_URLS:
        response = client.get(url)
        assert response.status_code == 401
        assert response.json() == {"code": "bad_key", "message": "Unknown key"}


def test_docs_answer_with_a_bodyless_response_the_auth_does_not_declare():
    def auth(request):
        return NoContent()

    client = TestClient(HattoriAPI(docs_auth=auth))
    for url in DOCS_URLS:
        response = client.get(url)
        assert response.status_code == 204
        assert response.content == b""


def test_docs_run_async_auth():
    async def auth(request):
        return request.GET.get("key")

    client = TestClient(HattoriAPI(auth=auth))
    for url in DOCS_URLS:
        assert client.get(url).status_code == 401
        assert client.get(f"{url}?key=k").status_code == 200


def test_docs_decorator_wraps_the_guarded_docs():
    def decorator(view):
        def wrapper(request, **kwargs):
            response = view(request, **kwargs)
            response["X-Docs"] = "yes"
            return response

        return wrapper

    client = TestClient(HattoriAPI(auth=Key(), docs_decorator=decorator))
    for url in DOCS_URLS:
        response = client.get(url)
        assert response.status_code == 401
        assert response["X-Docs"] == "yes"
