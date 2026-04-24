"""Parameterized-generic response schemas under APIReturn.

Each domain error subclass pins its own `ErrorResponse[Literal[...]]` schema,
preserving per-code narrowing in the OpenAPI spec.
"""

from enum import Enum
from typing import Generic, Literal, TypeVar

from hattori import APIReturn, HattoriAPI, Schema
from hattori.testing import TestClient

C = TypeVar("C", default=str)


class ErrorResponse(Schema, Generic[C]):
    code: C
    message: str


class GetError(Enum):
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"


class SyncError(Enum):
    ACCOUNT_BELONGS_TO_ANOTHER = "account_belongs_to_another"
    LIKELY_DUPLICATE = "likely_duplicate"


class NotFound(APIReturn[ErrorResponse[Literal[GetError.NOT_FOUND]]]):
    code = 404


class Forbidden(APIReturn[ErrorResponse[Literal[GetError.FORBIDDEN]]]):
    code = 403


class CustomError(APIReturn[ErrorResponse[Literal["custom_error"]]]):
    code = 400


class AccountBelongsToAnother(
    APIReturn[ErrorResponse[Literal[SyncError.ACCOUNT_BELONGS_TO_ANOTHER]]]
):
    code = 409


class LikelyDuplicate(
    APIReturn[ErrorResponse[Literal[SyncError.LIKELY_DUPLICATE]]]
):
    code = 409


api = HattoriAPI()


@api.get("/items/{item_id}")
def get_item(request, item_id: int) -> dict | NotFound | Forbidden:
    if item_id == 0:
        return NotFound(ErrorResponse(code=GetError.NOT_FOUND, message="Not found"))
    if item_id == -1:
        return Forbidden(ErrorResponse(code=GetError.FORBIDDEN, message="Forbidden"))
    return {"id": item_id}


@api.get("/string-literal")
def string_literal(request) -> dict | CustomError:
    return CustomError(ErrorResponse(code="custom_error", message="Bad"))


@api.post("/sync")
def sync_account(
    request,
) -> dict | AccountBelongsToAnother | LikelyDuplicate:
    return {"ok": True}


client = TestClient(api)


class TestParameterizedGenericResponses:
    def test_enum_literal_404(self):
        response = client.get("/items/0")
        assert response.status_code == 404
        assert response.json() == {"code": "not_found", "message": "Not found"}

    def test_enum_literal_403(self):
        response = client.get("/items/-1")
        assert response.status_code == 403
        assert response.json() == {"code": "forbidden", "message": "Forbidden"}

    def test_success_200(self):
        response = client.get("/items/1")
        assert response.status_code == 200
        assert response.json() == {"id": 1}

    def test_string_literal(self):
        response = client.get("/string-literal")
        assert response.status_code == 400
        assert response.json() == {"code": "custom_error", "message": "Bad"}


class TestSchemaCleanNaming:
    def test_literal_string_naming(self):
        model = ErrorResponse[Literal["not_found"]]
        assert model.__name__ == "ErrorResponse_not_found"
        assert model.__qualname__ == "ErrorResponse_not_found"

    def test_literal_enum_naming(self):
        model = ErrorResponse[Literal[GetError.NOT_FOUND]]
        assert model.__name__ == "ErrorResponse_not_found"

    def test_multiple_literal_values(self):
        model = ErrorResponse[Literal[GetError.NOT_FOUND, GetError.FORBIDDEN]]
        assert model.__name__ == "ErrorResponse_not_found_forbidden"

    def test_plain_type_keeps_default_name(self):
        model = ErrorResponse[str]
        assert "ErrorResponse" in model.__name__

    def test_openapi_schema_uses_clean_names(self):
        schema = api.get_openapi_schema()
        defs = schema.get("components", {}).get("schemas", {})
        def_names = set(defs.keys())
        assert "ErrorResponse_not_found" in def_names
        assert "ErrorResponse_forbidden" in def_names

    def test_openapi_responses_reference_clean_names(self):
        schema = api.get_openapi_schema()
        item_get = schema["paths"]["/api/items/{item_id}"]["get"]
        resp_404 = item_get["responses"][404]
        ref = resp_404["content"]["application/json"]["schema"]["$ref"]
        assert ref == "#/components/schemas/ErrorResponse_not_found"


class TestDuplicateStatusCodesCombined:
    """Multiple response arms with the same status code should be combined, not overwritten."""

    def test_sync_success(self):
        response = client.post("/sync")
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_openapi_409_has_both_variants(self):
        schema = api.get_openapi_schema()
        sync_post = schema["paths"]["/api/sync"]["post"]
        resp_409 = sync_post["responses"][409]
        content_schema = resp_409["content"]["application/json"]["schema"]
        assert "anyOf" not in content_schema
        variants = content_schema["oneOf"]
        assert len(variants) == 2
        assert content_schema["discriminator"] == {
            "propertyName": "code",
            "mapping": {
                "account_belongs_to_another": "#/components/schemas/ErrorResponse_account_belongs_to_another",
                "likely_duplicate": "#/components/schemas/ErrorResponse_likely_duplicate",
            },
        }

    def test_both_409_variants_in_components(self):
        schema = api.get_openapi_schema()
        defs = schema.get("components", {}).get("schemas", {})
        def_names = set(defs.keys())
        assert "ErrorResponse_account_belongs_to_another" in def_names
        assert "ErrorResponse_likely_duplicate" in def_names
