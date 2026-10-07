"""The 401 and 403 the framework answers on its own are part of the contract.

When every auth on an operation declines, the framework answers 401; when a
permission's ``check`` returns a falsy value, or cookie auth fails its CSRF
check, it answers 403. All three carry the ``HttpError`` body. An operation
used to document none of them: its spec listed a 401 or 403 only where an auth
or permission class declared a typed response for that code.
"""

from http import HTTPStatus
from typing import Literal, Self

import pytest
from django.views.decorators.csrf import csrf_exempt
from openapi_contract import export_contract, validate_response

from hattori import ApiError, BasePermission, HattoriAPI, HttpErrorBody, Router, Schema
from hattori.errors import HttpError, get_http_error_model, set_http_error_model
from hattori.security import APIKeyCookie, APIKeyHeader, HttpBearer
from hattori.testing import TestAsyncClient, TestClient

HTTP_ERROR = {"$ref": "#/components/schemas/HttpErrorResponse"}
BAD_TOKEN = {"$ref": "#/components/schemas/BadToken"}
NOT_ADMIN = {"$ref": "#/components/schemas/NotAdmin"}

ADMIN = {"Authorization": "Bearer admin"}
MEMBER = {"Authorization": "Bearer member"}


class Out(Schema):
    ok: bool


class BadToken(ApiError):
    code = 401
    error_code = "bad_token"
    message = "Token invalid"


class NotAdmin(ApiError):
    code = 403
    error_code = "not_admin"
    message = "Admin role required"


class PlainBearer(HttpBearer):
    def authenticate(self, request, token: str) -> str | None:
        return token if token in ("admin", "member") else None


class TypedBearer(HttpBearer):
    def authenticate(self, request, token: str) -> str | BadToken:
        return token if token in ("admin", "member") else BadToken()


class IsAdmin(BasePermission):
    message = "Admins only"

    def check(self, request) -> bool:
        return request.auth == "admin"


class IsAdminTyped(BasePermission):
    def check(self, request) -> Literal[True] | NotAdmin:
        return True if request.auth == "admin" else NotAdmin()


class IsAdminEitherWay(BasePermission):
    def check(self, request) -> bool | NotAdmin:
        if request.auth == "admin":
            return True
        return NotAdmin() if request.headers.get("X-Typed") else False


class Problem(HttpErrorBody):
    code: str
    message: str

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        status = HTTPStatus(error.status_code)
        return cls(code=status.name.lower(), message=str(error))


@pytest.fixture
def problem_model():
    original = get_http_error_model()
    set_http_error_model(Problem)
    yield
    set_http_error_model(original)


def _api(**kwargs) -> HattoriAPI:
    api = HattoriAPI()

    @api.get("/view", **kwargs)
    def view(request) -> Out:
        return Out(ok=True)

    return api


def _schema(document, status, path="/api/view", method="get"):
    response = document["paths"][path][method]["responses"][status]
    return response["content"]["application/json"]["schema"]


# ==========================================================================
# 401: every auth declined
# ==========================================================================


def test_untyped_auth_documents_the_default_401():
    api = _api(auth=PlainBearer())
    document = export_contract(api)

    response = TestClient(api).get("/view")

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
    validate_response(document, "/api/view", response)
    assert _schema(document, "401") == HTTP_ERROR
    responses = document["paths"]["/api/view"]["get"]["responses"]
    assert responses["401"]["description"] == "Unauthorized"


def test_default_401_is_documented_beside_a_typed_one():
    api = _api(auth=TypedBearer())
    document = export_contract(api)
    client = TestClient(api)

    # HttpBearer declines a request with no token before authenticate() runs.
    declined = client.get("/view")
    rejected = client.get("/view", headers={"Authorization": "Bearer nope"})

    assert declined.json() == {"detail": "Unauthorized"}
    assert rejected.json() == {"code": "bad_token", "message": "Token invalid"}
    validate_response(document, "/api/view", declined)
    validate_response(document, "/api/view", rejected)
    assert _schema(document, "401") == {"anyOf": [BAD_TOKEN, HTTP_ERROR]}


def test_default_401_is_documented_once_for_several_auths():
    class HeaderKey(APIKeyHeader):
        param_name = "X-Key"

        def authenticate(self, request, key):
            return key

    document = export_contract(_api(auth=[PlainBearer(), HeaderKey()]))

    assert _schema(document, "401") == HTTP_ERROR


def test_function_auth_documents_the_default_401():
    def by_header(request):
        return request.headers.get("X-User")

    api = _api(auth=by_header)
    document = export_contract(api)

    response = TestClient(api).get("/view")

    assert response.status_code == 401
    validate_response(document, "/api/view", response)


def test_operations_without_auth_document_no_401():
    api = HattoriAPI(auth=PlainBearer())

    @api.get("/public", auth=None)
    def public(request) -> Out:
        return Out(ok=True)

    responses = export_contract(api)["paths"]["/api/public"]["get"]["responses"]

    assert set(responses) == {"200"}
    assert set(export_contract(_api())["paths"]["/api/view"]["get"]["responses"]) == {
        "200"
    }


def test_inherited_auth_and_permissions_document_the_defaults():
    api = HattoriAPI(auth=PlainBearer())
    router = Router(permissions=[IsAdmin()])

    @router.get("/view")
    def view(request) -> Out:
        return Out(ok=True)

    api.add_router("/nested", router)
    document = export_contract(api)

    assert _schema(document, "401", "/api/nested/view") == HTTP_ERROR
    assert _schema(document, "403", "/api/nested/view") == HTTP_ERROR


# ==========================================================================
# 403: a permission's check returned a falsy value
# ==========================================================================


def test_falsy_permission_documents_the_default_403():
    api = _api(auth=PlainBearer(), permissions=[IsAdmin()])
    document = export_contract(api)

    response = TestClient(api).get("/view", headers=MEMBER)

    assert response.status_code == 403
    assert response.json() == {"detail": "Admins only"}
    validate_response(document, "/api/view", response)
    assert _schema(document, "403") == HTTP_ERROR


def test_permission_that_cannot_be_falsy_documents_only_its_typed_403():
    api = _api(auth=PlainBearer(), permissions=[IsAdminTyped()])
    document = export_contract(api)

    response = TestClient(api).get("/view", headers=MEMBER)

    assert response.json() == {"code": "not_admin", "message": "Admin role required"}
    validate_response(document, "/api/view", response)
    assert _schema(document, "403") == NOT_ADMIN


def test_default_403_is_documented_beside_a_typed_one():
    api = _api(auth=PlainBearer(), permissions=[IsAdminEitherWay()])
    document = export_contract(api)
    client = TestClient(api)

    untyped = client.get("/view", headers=MEMBER)
    typed = client.get("/view", headers={**MEMBER, "X-Typed": "1"})

    assert untyped.json() == {"detail": "Forbidden"}
    assert typed.json() == {"code": "not_admin", "message": "Admin role required"}
    validate_response(document, "/api/view", untyped)
    validate_response(document, "/api/view", typed)
    assert _schema(document, "403") == {"anyOf": [NOT_ADMIN, HTTP_ERROR]}


def test_one_falsy_permission_among_typed_ones_documents_the_default_403():
    class HasBetaAccess(BasePermission):
        message = "Beta access required"

        def check(self, request) -> bool:
            return "X-Beta" in request.headers

    api = _api(auth=PlainBearer(), permissions=[IsAdminTyped(), HasBetaAccess()])
    document = export_contract(api)
    client = TestClient(api)

    # A member is stopped by the first permission, an admin by the second.
    typed = client.get("/view", headers=MEMBER)
    untyped = client.get("/view", headers=ADMIN)
    allowed = client.get("/view", headers={**ADMIN, "X-Beta": "1"})

    assert typed.json() == {"code": "not_admin", "message": "Admin role required"}
    assert untyped.status_code == 403
    assert untyped.json() == {"detail": "Beta access required"}
    assert allowed.status_code == 200
    validate_response(document, "/api/view", typed)
    validate_response(document, "/api/view", untyped)
    assert _schema(document, "403") == {"anyOf": [NOT_ADMIN, HTTP_ERROR]}


def test_operations_without_permissions_document_no_403():
    responses = export_contract(_api(auth=PlainBearer()))["paths"]["/api/view"]["get"][
        "responses"
    ]

    assert set(responses) == {"200", "401"}


def _permission(annotation):
    class Permission(BasePermission):
        def check(self, request):
            return True

    if annotation is not ...:
        Permission.check.__annotations__["return"] = annotation
    return Permission()


@pytest.mark.parametrize(
    "annotation",
    [
        ...,  # no annotation at all
        bool,
        None,
        bool | None,
        bool | NotAdmin,
        Literal[True, False],
        Literal[True] | None,
        Literal[0],
        int,
        object,
        "NoSuchName",  # a forward reference that never resolves
    ],
)
def test_check_that_may_return_falsy(annotation):
    assert _permission(annotation).can_return_falsy is True


@pytest.mark.parametrize(
    "annotation",
    [
        Literal[True],
        Literal[True] | NotAdmin,
        Literal["ok", 1],
        NotAdmin,
        NotAdmin | BadToken,
    ],
)
def test_check_that_cannot_return_falsy(annotation):
    assert _permission(annotation).can_return_falsy is False


def test_async_check_is_read_like_a_sync_one():
    class AsyncTyped(BasePermission):
        async def check(self, request) -> Literal[True] | NotAdmin:
            return True

    class AsyncBool(BasePermission):
        async def check(self, request) -> bool:
            return True

    assert AsyncTyped().can_return_falsy is False
    assert AsyncBool().can_return_falsy is True


type AlwaysAllowed = Literal[True]
type Verdict = AlwaysAllowed | NotAdmin
type Checked = bool | NotAdmin
type Principal = str | BadToken


def test_check_annotation_is_read_through_type_aliases():
    assert _permission(AlwaysAllowed).can_return_falsy is False
    assert _permission(Verdict).can_return_falsy is False
    assert _permission(Checked).can_return_falsy is True
    assert _permission(Verdict | None).can_return_falsy is True


def test_typed_responses_behind_a_type_alias_are_documented_and_sent():
    class AliasedBearer(HttpBearer):
        def authenticate(self, request, token: str) -> Principal:
            return token if token in ("admin", "member") else BadToken()

    class AliasedIsAdmin(BasePermission):
        def check(self, request) -> Verdict:
            return True if request.auth == "admin" else NotAdmin()

    api = _api(auth=AliasedBearer(), permissions=[AliasedIsAdmin()])
    document = export_contract(api)
    client = TestClient(api)

    rejected = client.get("/view", headers={"Authorization": "Bearer nope"})
    denied = client.get("/view", headers=MEMBER)

    assert rejected.status_code == 401
    assert rejected.json() == {"code": "bad_token", "message": "Token invalid"}
    assert denied.status_code == 403
    assert denied.json() == {"code": "not_admin", "message": "Admin role required"}
    validate_response(document, "/api/view", rejected)
    validate_response(document, "/api/view", denied)
    assert _schema(document, "401") == {"anyOf": [BAD_TOKEN, HTTP_ERROR]}
    assert _schema(document, "403") == NOT_ADMIN


# What an alias of a name imported under TYPE_CHECKING looks like at runtime.
type OnlyForTheTypeChecker = NotImportedAtRuntime  # noqa: F821


def test_alias_that_cannot_be_resolved_keeps_the_arms_beside_it():
    class Bearer(HttpBearer):
        def authenticate(self, request, token: str) -> OnlyForTheTypeChecker | BadToken:
            return token if token in ("admin", "member") else BadToken()

    class IsAdminUnresolved(BasePermission):
        def check(self, request) -> OnlyForTheTypeChecker | NotAdmin:
            return True if request.auth == "admin" else NotAdmin()

    api = _api(auth=Bearer(), permissions=[IsAdminUnresolved()])
    document = export_contract(api)
    client = TestClient(api)

    rejected = client.get("/view", headers={"Authorization": "Bearer nope"})
    denied = client.get("/view", headers=MEMBER)

    assert rejected.json() == {"code": "bad_token", "message": "Token invalid"}
    assert denied.json() == {"code": "not_admin", "message": "Admin role required"}
    validate_response(document, "/api/view", rejected)
    validate_response(document, "/api/view", denied)
    # What the alias names is unknown, so it may be falsy.
    assert IsAdminUnresolved().can_return_falsy is True
    assert _schema(document, "403") == {"anyOf": [NOT_ADMIN, HTTP_ERROR]}


@pytest.mark.asyncio
async def test_async_operation_answers_the_documented_defaults():
    class AsyncBearer(HttpBearer):
        async def authenticate(self, request, token: str) -> str | None:
            return token if token in ("admin", "member") else None

    class AsyncIsAdmin(BasePermission):
        async def check(self, request) -> bool:
            return request.auth == "admin"

    api = HattoriAPI()

    @api.get("/view", auth=AsyncBearer(), permissions=[AsyncIsAdmin()])
    async def view(request) -> Out:
        return Out(ok=True)

    document = export_contract(api)
    client = TestAsyncClient(api)

    declined = await client.get("/view")
    denied = await client.get("/view", headers=MEMBER)
    allowed = await client.get("/view", headers=ADMIN)

    assert declined.status_code == 401
    assert denied.status_code == 403
    assert allowed.status_code == 200
    validate_response(document, "/api/view", declined)
    validate_response(document, "/api/view", denied)
    assert _schema(document, "401") == HTTP_ERROR
    assert _schema(document, "403") == HTTP_ERROR


# ==========================================================================
# 403: cookie auth failed its CSRF check
# ==========================================================================


class CookieAuth(APIKeyCookie):
    def authenticate(self, request, key):
        return key


class CsrfEnforcingClient(TestClient):
    def _build_request(self, *args, **kwargs):
        request = super()._build_request(*args, **kwargs)
        request._dont_enforce_csrf_checks = False
        return request


def _cookie_api(**auth_kwargs) -> HattoriAPI:
    api = HattoriAPI(auth=CookieAuth(**auth_kwargs))

    @api.post("/view")
    def create(request) -> Out:
        return Out(ok=True)

    @api.get("/view")
    def read(request) -> Out:
        return Out(ok=True)

    @api.post("/exempt")
    @csrf_exempt
    def exempt(request) -> Out:
        return Out(ok=True)

    return api


def test_cookie_auth_documents_the_csrf_403_on_unsafe_methods():
    api = _cookie_api()
    document = export_contract(api)

    response = CsrfEnforcingClient(api).post("/view")

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF check Failed"}
    validate_response(document, "/api/view", response, method="post")
    assert _schema(document, "403", method="post") == HTTP_ERROR


def test_csrf_403_is_not_documented_where_the_check_cannot_fail():
    paths = export_contract(_cookie_api())["paths"]

    assert "403" not in paths["/api/view"]["get"]["responses"]
    assert "403" not in paths["/api/exempt"]["post"]["responses"]
    without_csrf = export_contract(_cookie_api(csrf=False))["paths"]
    assert "403" not in without_csrf["/api/view"]["post"]["responses"]


def test_csrf_403_is_documented_on_the_unsafe_methods_of_an_operation_only():
    api = HattoriAPI(auth=CookieAuth())

    @api.api_operation(["GET", "POST"], "/view")
    def view(request) -> Out:
        return Out(ok=True)

    document = export_contract(api)
    client = CsrfEnforcingClient(api)
    cookie = {"key": "secret"}

    read = client.get("/view", COOKIES=cookie)
    rejected = client.post("/view", COOKIES=cookie)

    assert read.status_code == 200
    assert rejected.status_code == 403
    validate_response(document, "/api/view", rejected, method="post")
    assert _schema(document, "403", method="post") == HTTP_ERROR
    assert "403" not in document["paths"]["/api/view"]["get"]["responses"]


def test_responses_without_a_method_cover_every_method_of_the_operation():
    api = HattoriAPI(auth=CookieAuth())

    @api.api_operation(["GET", "POST"], "/view")
    def view(request) -> Out:
        return Out(ok=True)

    schema = api.get_openapi_schema()
    [operation] = api.default_router.path_operations["/view"].operations

    assert 403 in schema.responses(operation)
    assert 403 not in schema.responses(operation, "GET")


# ==========================================================================
# The body is the installed HttpError model
# ==========================================================================


def test_custom_model_shapes_the_default_401_and_403(problem_model):
    api = _api(auth=TypedBearer(), permissions=[IsAdmin()])
    document = export_contract(api)
    client = TestClient(api)
    problem = {"$ref": "#/components/schemas/Problem"}

    declined = client.get("/view")
    denied = client.get("/view", headers=MEMBER)

    assert declined.json() == {"code": "unauthorized", "message": "Unauthorized"}
    assert denied.json() == {"code": "forbidden", "message": "Admins only"}
    validate_response(document, "/api/view", declined)
    validate_response(document, "/api/view", denied)
    assert _schema(document, "401") == {"anyOf": [BAD_TOKEN, problem]}
    assert _schema(document, "403") == problem
    assert "HttpErrorResponse" not in document["components"]["schemas"]


def test_passing_request_is_still_answered_by_the_view():
    api = _api(auth=TypedBearer(), permissions=[IsAdmin()])

    response = TestClient(api).get("/view", headers=ADMIN)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
