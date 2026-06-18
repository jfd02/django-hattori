"""End-to-end tests for the permissions layer.

Permissions run *after* authentication (so ``request.auth`` is populated), with
AND semantics, and each ``check`` receives the route's path parameters. A falsy
result is a ``403``; an ``APIReturn`` short-circuits to that typed response.
"""

from enum import Enum
from typing import Literal

import pytest

from hattori import ApiError, BasePermission, Forbidden, HattoriAPI, Schema
from hattori.errors import AuthorizationError
from hattori.security import HttpBearer
from hattori.testing import TestAsyncClient, TestClient

# --------------------------------------------------------------------------
# A tiny finance-app domain: users belong to households with a role on the edge.
# --------------------------------------------------------------------------

# household_id -> {username: role}
MEMBERSHIPS: dict[int, dict[str, str]] = {
    1: {"alice": "admin", "bob": "member"},
    2: {"carol": "admin"},
}


class TokenAuth(HttpBearer):
    """Trivial bearer auth: the token *is* the username."""

    def authenticate(self, request, token: str):
        return token or None


class Out(Schema):
    ok: bool


class Whoami(Schema):
    auth: str


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------


class IsHouseholdMember(BasePermission):
    message = "Not a member of this household"

    def check(self, request, household_id) -> bool:
        members = MEMBERSHIPS.get(int(household_id), {})
        return request.auth in members


class IsHouseholdAdmin(BasePermission):
    message = "Must be a household admin"

    def check(self, request, household_id) -> bool:
        members = MEMBERSHIPS.get(int(household_id), {})
        return members.get(request.auth) == "admin"


class NotAdmin(ApiError):
    code = 403
    error_code = "not_admin"
    message = "Admin role required"


class IsHouseholdAdminTyped(BasePermission):
    """Same check, but returns a typed 403 so it lands in the OpenAPI spec."""

    def check(self, request, household_id) -> Literal[True] | NotAdmin:
        members = MEMBERSHIPS.get(int(household_id), {})
        if members.get(request.auth) == "admin":
            return True
        return NotAdmin()


# ==========================================================================
# Headline scenario: JWT + household-scoped admin role
# ==========================================================================

household_api = HattoriAPI(urls_namespace="perm-household")


@household_api.get(
    "/households/{household_id}/budget",
    auth=TokenAuth(),
    permissions=[IsHouseholdAdmin()],
)
def budget(request, household_id: int) -> Out:
    return Out(ok=True)


# Reading is open to any member; only admins may write — different permission per
# operation on the same resource.
@household_api.get(
    "/households/{household_id}/ledger",
    auth=TokenAuth(),
    permissions=[IsHouseholdMember()],
)
def read_ledger(request, household_id: int) -> Out:
    return Out(ok=True)


household_client = TestClient(household_api)


# A separate module-level API for the OpenAPI test: get_openapi_schema() reverses
# the api root, which needs the namespace registered at import time.
openapi_api = HattoriAPI(urls_namespace="perm-openapi")


@openapi_api.get(
    "/households/{household_id}/doc",
    auth=TokenAuth(),
    permissions=[IsHouseholdAdminTyped()],
)
def documented(request, household_id: int) -> Out:
    return Out(ok=True)


openapi_client = TestClient(openapi_api)


def _bearer(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user}"}


def test_admin_of_the_household_is_allowed():
    r = household_client.get("/households/1/budget", headers=_bearer("alice"))
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_member_but_not_admin_is_forbidden():
    r = household_client.get("/households/1/budget", headers=_bearer("bob"))
    assert r.status_code == 403
    assert r.json() == {"detail": "Must be a household admin"}


def test_admin_of_a_different_household_is_forbidden():
    # carol is admin of household 2, a stranger to household 1.
    r = household_client.get("/households/1/budget", headers=_bearer("carol"))
    assert r.status_code == 403


def test_unauthenticated_is_rejected_before_permissions_run():
    # No token -> auth fails with 401; the permission never runs.
    r = household_client.get("/households/1/budget")
    assert r.status_code == 401


def test_member_can_read_but_stranger_cannot():
    # bob is a plain member: allowed to read the ledger, denied the admin budget.
    assert (
        household_client.get("/households/1/ledger", headers=_bearer("bob")).status_code
        == 200
    )
    assert (
        household_client.get(
            "/households/1/ledger", headers=_bearer("carol")
        ).status_code
        == 403
    )


# ==========================================================================
# Result protocol: truthy / falsy / typed APIReturn / custom message
# ==========================================================================


def test_allow_and_deny_with_default_message():
    api = HattoriAPI(urls_namespace="perm-bool")

    class Gate(BasePermission):
        def check(self, request, allow) -> bool:
            return allow == "yes"

    @api.get("/{allow}", permissions=[Gate()])
    def view(request, allow: str) -> Out:
        return Out(ok=True)

    client = TestClient(api)
    assert client.get("/yes").status_code == 200
    denied = client.get("/no")
    assert denied.status_code == 403
    # Falls back to BasePermission.message ("Forbidden") by default.
    assert denied.json() == {"detail": "Forbidden"}


def test_none_return_is_treated_as_denied():
    api = HattoriAPI(urls_namespace="perm-none")

    class AlwaysNone(BasePermission):
        message = "nope"

        def check(self, request):
            return None  # forgetting to return True denies, fail-closed

    @api.get("/x", permissions=[AlwaysNone()])
    def view(request) -> Out:
        return Out(ok=True)

    r = TestClient(api).get("/x")
    assert r.status_code == 403
    assert r.json() == {"detail": "nope"}


def test_typed_apireturn_short_circuits():
    api = HattoriAPI(urls_namespace="perm-typed")

    @api.get(
        "/households/{household_id}/typed",
        auth=TokenAuth(),
        permissions=[IsHouseholdAdminTyped()],
    )
    def view(request, household_id: int) -> Out:
        return Out(ok=True)

    client = TestClient(api)
    ok = client.get("/households/1/typed", headers=_bearer("alice"))
    assert ok.status_code == 200

    denied = client.get("/households/1/typed", headers=_bearer("bob"))
    assert denied.status_code == 403
    assert denied.json() == {"code": "not_admin", "message": "Admin role required"}


def test_check_returning_forbidden_httperror():
    api = HattoriAPI(urls_namespace="perm-httperror")

    class PermError(Enum):
        DENIED = "denied"

    class Denied(Forbidden[Literal[PermError.DENIED]]):
        message = "go away"

    class Gate(BasePermission):
        def check(self, request) -> Literal[True] | Denied:
            return Denied()

    @api.get("/x", permissions=[Gate()])
    def view(request) -> Out:
        return Out(ok=True)

    r = TestClient(api).get("/x")
    assert r.status_code == 403
    assert r.json()["code"] == "denied"


def test_exception_in_check_is_handled():
    api = HattoriAPI(urls_namespace="perm-raises")

    class Boom(BasePermission):
        def check(self, request):
            raise AuthorizationError(message="blown up")

    @api.get("/x", permissions=[Boom()])
    def view(request) -> Out:
        return Out(ok=True)

    r = TestClient(api).get("/x")
    assert r.status_code == 403
    assert r.json() == {"detail": "blown up"}


# ==========================================================================
# AND composition + ordering
# ==========================================================================


def test_permissions_compose_with_and_semantics_and_short_circuit():
    api = HattoriAPI(urls_namespace="perm-and")
    calls: list[str] = []

    class First(BasePermission):
        def __init__(self, allow):
            self.allow = allow
            super().__init__()

        def check(self, request):
            calls.append("first")
            return self.allow

    class Second(BasePermission):
        def check(self, request):
            calls.append("second")
            return True

    @api.get("/pass", permissions=[First(True), Second()])
    def view_pass(request) -> Out:
        return Out(ok=True)

    @api.get("/fail", permissions=[First(False), Second()])
    def view_fail(request) -> Out:
        return Out(ok=True)

    client = TestClient(api)

    calls.clear()
    assert client.get("/pass").status_code == 200
    assert calls == ["first", "second"]

    # First denies -> Second must not be evaluated (short-circuit).
    calls.clear()
    assert client.get("/fail").status_code == 403
    assert calls == ["first"]


def test_permission_runs_after_auth():
    api = HattoriAPI(urls_namespace="perm-after-auth")
    ran: list[str] = []

    class FailingAuth(HttpBearer):
        def authenticate(self, request, token: str):
            return None  # always 401

    class Recorder(BasePermission):
        def check(self, request):
            ran.append("perm")
            return True

    @api.get("/x", auth=FailingAuth(), permissions=[Recorder()])
    def view(request) -> Out:
        return Out(ok=True)

    r = TestClient(api).get("/x", headers=_bearer("alice"))
    assert r.status_code == 401
    assert ran == []  # permission never reached


# ==========================================================================
# request.auth visibility + the "loader" stash pattern
# ==========================================================================


def test_permission_sees_request_auth():
    api = HattoriAPI(urls_namespace="perm-sees-auth")

    class RequireAlice(BasePermission):
        def check(self, request) -> bool:
            return request.auth == "alice"

    @api.get("/x", auth=TokenAuth(), permissions=[RequireAlice()])
    def view(request) -> Whoami:
        return Whoami(auth=request.auth)

    client = TestClient(api)
    assert client.get("/x", headers=_bearer("alice")).json() == {"auth": "alice"}
    assert client.get("/x", headers=_bearer("bob")).status_code == 403


def test_permission_can_stash_loaded_object_for_the_view():
    """The recommended pattern: authz and data-load are one query."""
    api = HattoriAPI(urls_namespace="perm-loader")

    class Membership(Schema):
        household_id: int
        role: str

    class LoadAdminMembership(BasePermission):
        def check(self, request, household_id) -> bool:
            members = MEMBERSHIPS.get(int(household_id), {})
            role = members.get(request.auth)
            if role != "admin":
                return False
            # Stash it so the view doesn't re-query.
            request.membership = Membership(household_id=int(household_id), role=role)
            return True

    @api.get(
        "/households/{household_id}/loader",
        auth=TokenAuth(),
        permissions=[LoadAdminMembership()],
    )
    def view(request, household_id: int) -> Membership:
        return request.membership

    client = TestClient(api)
    r = client.get("/households/1/loader", headers=_bearer("alice"))
    assert r.status_code == 200
    assert r.json() == {"household_id": 1, "role": "admin"}


# ==========================================================================
# Path-param introspection variants
# ==========================================================================


def test_check_signature_variants_receive_the_right_kwargs():
    api = HattoriAPI(urls_namespace="perm-sig")
    captured: dict[str, object] = {}

    class Global(BasePermission):
        # No path params: usable on any route.
        def check(self, request) -> bool:
            captured["global"] = True
            return True

    class Scoped(BasePermission):
        def check(self, request, household_id) -> bool:
            captured["scoped"] = household_id
            return True

    class VarKw(BasePermission):
        def check(self, request, **path) -> bool:
            captured["varkw"] = dict(path)
            return True

    @api.get(
        "/households/{household_id}/items/{item_id}",
        permissions=[Global(), Scoped(), VarKw()],
    )
    def view(request, household_id: int, item_id: int) -> Out:
        return Out(ok=True)

    r = TestClient(api).get("/households/7/items/9")
    assert r.status_code == 200
    assert captured["global"] is True
    # raw path values are strings (documented behavior)
    assert captured["scoped"] == "7"
    assert captured["varkw"] == {"household_id": "7", "item_id": "9"}


def test_single_permission_instance_is_accepted():
    api = HattoriAPI(urls_namespace="perm-single")

    class Deny(BasePermission):
        def check(self, request) -> bool:
            return False

    @api.get("/x", permissions=Deny())  # not a list
    def view(request) -> Out:
        return Out(ok=True)

    assert TestClient(api).get("/x").status_code == 403


# ==========================================================================
# Inheritance: router-level and api-level defaults + overrides
# ==========================================================================


def test_router_level_permissions_are_inherited_and_overridable():
    from hattori import Router

    class DenyAll(BasePermission):
        message = "router-denied"

        def check(self, request) -> bool:
            return False

    class AllowAll(BasePermission):
        def check(self, request) -> bool:
            return True

    api = HattoriAPI(urls_namespace="perm-router")
    router = Router(permissions=[DenyAll()])

    @router.get("/inherits")
    def inherits(request) -> Out:
        return Out(ok=True)

    @router.get("/overrides", permissions=[AllowAll()])
    def overrides(request) -> Out:
        return Out(ok=True)

    @router.get("/disables", permissions=None)
    def disables(request) -> Out:
        return Out(ok=True)

    api.add_router("/r", router)
    client = TestClient(api)

    assert client.get("/r/inherits").status_code == 403
    assert client.get("/r/inherits").json() == {"detail": "router-denied"}
    assert client.get("/r/overrides").status_code == 200
    # permissions=None explicitly opts out of the inherited permission
    assert client.get("/r/disables").status_code == 200


def test_api_level_permissions_apply_to_all_operations():
    class DenyAll(BasePermission):
        def check(self, request) -> bool:
            return False

    api = HattoriAPI(urls_namespace="perm-api-default", permissions=[DenyAll()])

    @api.get("/x")
    def view(request) -> Out:
        return Out(ok=True)

    @api.get("/open", permissions=None)
    def open_view(request) -> Out:
        return Out(ok=True)

    client = TestClient(api)
    assert client.get("/x").status_code == 403
    assert client.get("/open").status_code == 200


def test_mount_level_permissions_override():
    from hattori import Router

    class DenyAll(BasePermission):
        def check(self, request) -> bool:
            return False

    api = HattoriAPI(urls_namespace="perm-mount")
    router = Router()

    @router.get("/x")
    def view(request) -> Out:
        return Out(ok=True)

    api.add_router("/r", router, permissions=[DenyAll()])
    assert TestClient(api).get("/r/x").status_code == 403


# ==========================================================================
# OpenAPI documentation
# ==========================================================================


def test_typed_permission_is_documented_in_openapi():
    # path_prefix is passed explicitly so the schema doesn't depend on the demo
    # project's root urlconf registering this api's (custom) namespace.
    schema = openapi_api.get_openapi_schema(path_prefix="/api")
    responses = schema["paths"]["/api/households/{household_id}/doc"]["get"][
        "responses"
    ]
    # View declares Out -> 200; the permission contributes a typed 403 (NotAdmin).
    assert 200 in responses
    assert 403 in responses


# ==========================================================================
# Async permissions
# ==========================================================================


@pytest.mark.asyncio
async def test_async_permission_on_async_view():
    api = HattoriAPI(urls_namespace="perm-async-async")

    class AsyncAdmin(BasePermission):
        async def check(self, request, household_id) -> bool:
            members = MEMBERSHIPS.get(int(household_id), {})
            return members.get(request.auth) == "admin"

    @api.get(
        "/households/{household_id}/a",
        auth=TokenAuth(),
        permissions=[AsyncAdmin()],
    )
    async def view(request, household_id: int) -> Out:
        return Out(ok=True)

    client = TestAsyncClient(api)
    ok = await client.get("/households/1/a", headers=_bearer("alice"))
    assert ok.status_code == 200
    denied = await client.get("/households/1/a", headers=_bearer("bob"))
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_sync_permission_on_async_view():
    api = HattoriAPI(urls_namespace="perm-sync-async")

    @api.get(
        "/households/{household_id}/b",
        auth=TokenAuth(),
        permissions=[IsHouseholdAdmin()],
    )
    async def view(request, household_id: int) -> Out:
        return Out(ok=True)

    client = TestAsyncClient(api)
    assert (
        await client.get("/households/1/b", headers=_bearer("alice"))
    ).status_code == 200
    assert (
        await client.get("/households/1/b", headers=_bearer("bob"))
    ).status_code == 403


def test_async_permission_on_sync_view():
    api = HattoriAPI(urls_namespace="perm-async-sync")

    class AsyncAdmin(BasePermission):
        async def check(self, request, household_id) -> bool:
            members = MEMBERSHIPS.get(int(household_id), {})
            return members.get(request.auth) == "admin"

    @api.get(
        "/households/{household_id}/c",
        auth=TokenAuth(),
        permissions=[AsyncAdmin()],
    )
    def view(request, household_id: int) -> Out:
        return Out(ok=True)

    client = TestClient(api)
    assert client.get("/households/1/c", headers=_bearer("alice")).status_code == 200
    assert client.get("/households/1/c", headers=_bearer("bob")).status_code == 403
