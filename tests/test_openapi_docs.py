from django.conf import settings
from django.test import override_settings

from hattori import ApiError, BasePermission, HattoriAPI, Redoc, Swagger
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


def test_docs_take_the_api_permissions():
    client = TestClient(HattoriAPI(auth=Key(), permissions=[StaffOnly()]))
    for url in DOCS_URLS:
        assert client.get(f"{url}?key=guest").status_code == 403
        assert client.get(f"{url}?key=staff").status_code == 200


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
