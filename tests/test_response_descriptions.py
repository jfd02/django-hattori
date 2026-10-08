"""A response class's ``description`` is what the spec says of that status."""

from typing import Literal

from hattori import (
    Accepted,
    ApiError,
    APIReturn,
    BasePermission,
    Created,
    HattoriAPI,
    Router,
    Schema,
)
from hattori.security import APIKeyQuery
from hattori.testing import TestClient


class Out(Schema):
    id: int


class Missing(ApiError):
    code = 404
    error_code = "missing"
    message = "missing"
    description = "Nothing has this id."


class Hidden(ApiError):
    code = 404
    error_code = "hidden"
    message = "hidden"
    description = "It exists, but not for this caller."


class AlsoMissing(Missing):
    error_code = "also_missing"


class Undescribed(ApiError):
    code = 409
    error_code = "undescribed"
    message = "undescribed"


class Registered(Created[Out]):
    description = "The account was created and is ready to use."


class Queued(APIReturn[Out]):
    code = 202
    description = "Taken in; the work happens later."


def responses(api: HattoriAPI, path: str, method: str = "get") -> dict:
    return api.get_openapi_schema()["paths"][f"/api{path}"][method]["responses"]


def descriptions(api: HattoriAPI, path: str, method: str = "get") -> dict:
    return {
        code: response["description"]
        for code, response in responses(api, path, method).items()
    }


def test_description_of_a_response_class_is_documented():
    api = HattoriAPI()

    @api.get("/item")
    def item(request) -> Out | Missing | Queued | Registered:
        return Out(id=1)

    assert descriptions(api, "/item") == {
        200: "OK",
        404: "Nothing has this id.",
        202: "Taken in; the work happens later.",
        201: "The account was created and is ready to use.",
    }


def test_status_phrase_stands_in_where_no_description_is_declared():
    api = HattoriAPI()

    @api.get("/item")
    def item(request) -> Out | Undescribed | Created[Out] | Accepted[Out]:
        return Out(id=1)

    assert descriptions(api, "/item") == {
        200: "OK",
        409: "Conflict",
        201: "Created",
        202: "Accepted",
    }


def test_description_is_inherited():
    api = HattoriAPI()

    @api.get("/item")
    def item(request) -> Out | AlsoMissing:
        return Out(id=1)

    assert descriptions(api, "/item")[404] == "Nothing has this id."


def test_classes_sharing_a_status_are_described_one_after_the_other():
    api = HattoriAPI()

    @api.get("/item")
    def item(request) -> Out | Hidden | Missing | AlsoMissing:
        return Out(id=1)

    assert descriptions(api, "/item")[404] == (
        "It exists, but not for this caller.\n\nNothing has this id."
    )


class BadKey(ApiError):
    code = 401
    error_code = "bad_key"
    message = "bad key"
    description = "The key is unknown or has been revoked."


class Key(APIKeyQuery):
    def authenticate(self, request, key) -> str | BadKey:
        return key or BadKey()


def key_auth(request) -> str | BadKey:
    return request.GET.get("key") or BadKey()


class NotStaff(ApiError):
    code = 403
    error_code = "not_staff"
    message = "not staff"
    description = "Only staff may do this."


class StaffOnly(BasePermission):
    def check(self, request) -> Literal[True] | NotStaff:
        return True if request.auth == "staff" else NotStaff()


class AnyoneButHidden(BasePermission):
    def check(self, request) -> Literal[True] | Hidden:
        return True


def test_auth_and_permission_responses_are_described_too():
    api = HattoriAPI()

    @api.get("/class", auth=Key(), permissions=[StaffOnly()])
    def by_class(request) -> Out:
        return Out(id=1)

    @api.get("/function", auth=key_auth)
    def by_function(request) -> Out:
        return Out(id=1)

    assert descriptions(api, "/class") == {
        200: "OK",
        401: "The key is unknown or has been revoked.",
        403: "Only staff may do this.",
    }
    assert descriptions(api, "/function")[401] == (
        "The key is unknown or has been revoked."
    )


def test_description_stays_when_the_framework_adds_its_own_answer_to_the_status():
    api = HattoriAPI()

    @api.get("/item", auth=Key())
    def item(request) -> Out:
        return Out(id=1)

    unauthorized = responses(api, "/item")[401]
    assert unauthorized["description"] == "The key is unknown or has been revoked."
    assert unauthorized["content"]["application/json"]["schema"] == {
        "anyOf": [
            {"$ref": "#/components/schemas/BadKey"},
            {"$ref": "#/components/schemas/HttpErrorResponse"},
        ]
    }


def test_endpoint_is_described_before_its_auth_and_permissions():
    api = HattoriAPI()

    @api.get("/item", auth=Key(), permissions=[AnyoneButHidden()])
    def item(request) -> Out | Missing:
        return Out(id=1)

    assert descriptions(api, "/item")[404] == (
        "Nothing has this id.\n\nIt exists, but not for this caller."
    )


def test_inherited_auth_is_described_on_every_mount_of_a_router():
    router = Router()

    @router.get("/item")
    def item(request) -> Out | Missing:
        return Out(id=1)

    api = HattoriAPI(auth=Key())
    api.add_router("/one", router, url_name_prefix="one")
    api.add_router("/two", router, url_name_prefix="two")

    for path in ("/one/item", "/two/item"):
        assert descriptions(api, path) == {
            200: "OK",
            404: "Nothing has this id.",
            401: "The key is unknown or has been revoked.",
        }


def test_description_changes_nothing_that_is_sent():
    api = HattoriAPI()

    @api.get("/item")
    def item(request) -> Out | Missing:
        return Missing()

    response = TestClient(api).get("/item")
    assert response.status_code == 404
    assert response.json() == {"code": "missing", "message": "missing"}
