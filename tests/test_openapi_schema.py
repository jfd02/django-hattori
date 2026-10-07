import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated
from unittest.mock import Mock, patch
from uuid import uuid4

import pydantic
import pytest
from django.contrib.admin.views.decorators import staff_member_required
from django.test import Client, override_settings

from hattori import (
    APIReturn,
    Body,
    Field,
    File,
    Form,
    HattoriAPI,
    P,
    PathEx,
    Query,
    Router,
    Schema,
    UploadedFile,
)
from hattori.errors import ConfigError
from hattori.openapi import get_schema
from hattori.openapi.urls import get_openapi_urls
from hattori.renderers import JSONRenderer
from hattori.testing import TestClient

api = HattoriAPI()

VALIDATION_ERROR_422 = {
    "description": "Unprocessable Content",
    "content": {
        "application/json": {
            "schema": {
                "$ref": "#/components/schemas/ValidationErrorResponse",
            }
        }
    },
}


HTTP_ERROR_RESPONSE = {
    "properties": {"detail": {"title": "Detail", "type": "string"}},
    "required": ["detail"],
    "title": "HttpErrorResponse",
    "type": "object",
}


PARSE_ERROR_400 = {
    "description": "Bad Request",
    "content": {
        "application/json": {
            "schema": {
                "$ref": "#/components/schemas/HttpErrorResponse",
            }
        }
    },
}


class Payload(Schema):
    i: int
    f: float


class TypeA(Schema):
    a: str


class TypeB(Schema):
    b: str


AnnotatedStr = Annotated[
    str,
    pydantic.WithJsonSchema({
        "type": "string",
        "format": "custom-format",
        "example": "example_string",
    }),
]


def to_camel(string: str) -> str:
    words = string.split("_")
    return words[0].lower() + "".join(word.capitalize() for word in words[1:])


class Response(Schema):
    model_config = pydantic.ConfigDict(alias_generator=to_camel, populate_by_name=True)
    i: int
    f: float = Field(..., title="f title", description="f desc")


class DeprecatedExampleResult(Schema):
    i: str
    f: str


class PersonResult(Schema):
    uuid: str
    fullname: str


@api.post("/test")
def method(request, data: Payload) -> Response:
    return data.dict()


@api.post("/test-alias", by_alias=True)
def method_alias(request, data: Payload) -> Response:
    return data.dict()


@api.post("/test_list")
def method_list_response(request, data: list[Payload]) -> list[Response]:
    return []


@api.post("/test-body")
def method_body(request, i: int = Body(...), f: float = Body(...)) -> Response:
    return dict(i=i, f=f)


@api.post("/test-body-schema")
def method_body_schema(request, data: Payload) -> Response:
    return dict(i=data.i, f=data.f)


@api.get("/test-path/{int:i}/{f}")
def method_path(
    request,
    i: int,
    f: float,
) -> Response:
    return dict(i=i, f=f)


@api.get("/test-pathex/{path_ex}")
def method_pathex(
    request,
    path_ex: PathEx[
        AnnotatedStr,
        P(description="path_ex description"),
    ],
) -> AnnotatedStr:
    return path_ex


@api.post("/test-form")
def method_form(request, data: Payload = Form(...)) -> Response:
    return dict(i=data.i, f=data.f)


@api.post("/test-form-single")
def method_form_single(request, data: float = Form(...)) -> Response:
    return dict(i=int(data), f=data)


@api.post("/test-form-body")
def method_form_body(request, i: int = Form(10), s: str = Body("10")) -> Response:
    return dict(i=i, s=s)


@api.post("/test-form-file")
def method_form_file(
    request, files: list[UploadedFile], data: Payload = Form(...)
) -> Response:
    return dict(i=data.i, f=data.f)


@api.post("/test-body-file")
def method_body_file(
    request,
    files: list[UploadedFile],
    body: Payload = Body(...),
) -> Response:
    return dict(i=body.i, f=body.f)


@api.post("/test-union-type")
def method_union_payload(request, data: TypeA | TypeB) -> Response:
    return dict(i=data.i, f=data.f)


@api.post("/test-union-type-with-simple")
def method_union_payload_and_simple(request, data: int | TypeB) -> Response:
    return data.dict()


@api.post("/test-new-union-type")
def method_new_union_payload(request, data: TypeA | TypeB) -> Response:
    return dict(i=data.i, f=data.f)


@api.post(
    "/test-title-description/",
    tags=["a-tag"],
    summary="Best API Ever",
)
def method_test_title_description(
    request,
    param1: int = Query(..., title="param 1 title"),
    param2: str = Query("A Default", description="param 2 desc"),
    file: UploadedFile = File(..., description="file param desc"),
) -> Response:
    return dict(i=param1, f=param2)


@api.post("/test-deprecated-example-examples/")
def method_test_deprecated_example_examples(
    request,
    param1: int = Query(None, deprecated=True),
    param2: str = Query(..., example="Example Value"),
    param3: str = Query(
        ...,
        max_length=5,
        examples={
            "normal": {
                "summary": "A normal example",
                "description": "A **normal** string works correctly.",
                "value": "Foo",
            },
            "invalid": {
                "summary": "Invalid data is rejected with an error",
                "value": "MoreThan5Length",
            },
        },
    ),
    param4: int = Query(None, deprecated=True, include_in_schema=False),
) -> DeprecatedExampleResult:
    return dict(i=param2, f=param3)


def test_schema_views(client: Client):
    assert client.get("/api/").status_code == 404
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/openapi.json").status_code == 200


def test_schema_views_no_INSTALLED_APPS(client: Client):
    "Making sure that cdn and included js works fine"
    from django.conf import settings

    # removing hattori from settings:
    INSTALLED_APPS = [i for i in settings.INSTALLED_APPS if i != "hattori"]

    @override_settings(INSTALLED_APPS=INSTALLED_APPS)
    def call_docs():
        assert client.get("/api/docs").status_code == 200

    call_docs()


@pytest.fixture(scope="session")
def schema():
    return api.get_openapi_schema()


def test_schema(schema):
    method = schema["paths"]["/api/test"]["post"]

    assert method["requestBody"] == {
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Payload"}}
        },
        "required": True,
    }
    assert method["responses"] == {
        200: {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Response"}
                }
            },
            "description": "OK",
        },
        400: PARSE_ERROR_400,
        422: VALIDATION_ERROR_422,
    }
    assert schema.schemas == {
        "Response": {
            "title": "Response",
            "type": "object",
            "properties": {
                "i": {"title": "I", "type": "integer"},
                "f": {"description": "f desc", "title": "f title", "type": "number"},
            },
            "required": ["i", "f"],
        },
        "DeprecatedExampleResult": {
            "title": "DeprecatedExampleResult",
            "type": "object",
            "properties": {
                "i": {"title": "I", "type": "string"},
                "f": {"title": "F", "type": "string"},
            },
            "required": ["i", "f"],
        },
        "Payload": {
            "title": "Payload",
            "type": "object",
            "properties": {
                "i": {"title": "I", "type": "integer"},
                "f": {"title": "F", "type": "number"},
            },
            "required": ["i", "f"],
        },
        "TypeA": {
            "properties": {
                "a": {"title": "A", "type": "string"},
            },
            "required": ["a"],
            "title": "TypeA",
            "type": "object",
        },
        "TypeB": {
            "properties": {
                "b": {"title": "B", "type": "string"},
            },
            "required": ["b"],
            "title": "TypeB",
            "type": "object",
        },
        "ValidationErrorDetail": {
            "additionalProperties": True,
            "properties": {
                "loc": {
                    "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                    "title": "Loc",
                    "type": "array",
                },
                "msg": {"title": "Msg", "type": "string"},
                "type": {"title": "Type", "type": "string"},
            },
            "required": ["loc", "msg", "type"],
            "title": "ValidationErrorDetail",
            "type": "object",
        },
        "ValidationErrorResponse": {
            "properties": {
                "detail": {
                    "items": {"$ref": "#/components/schemas/ValidationErrorDetail"},
                    "title": "Detail",
                    "type": "array",
                }
            },
            "required": ["detail"],
            "title": "ValidationErrorResponse",
            "type": "object",
        },
        "HttpErrorResponse": HTTP_ERROR_RESPONSE,
    }


def test_schema_alias(schema):
    method = schema["paths"]["/api/test-alias"]["post"]

    assert method["requestBody"] == {
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Payload"}}
        },
        "required": True,
    }
    assert method["responses"] == {
        200: {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Response"}
                }
            },
            "description": "OK",
        },
        400: PARSE_ERROR_400,
        422: VALIDATION_ERROR_422,
    }
    # ::TODO:: this is currently broken if not all responses for same schema use the same by_alias
    """
    assert schema.schemas == {
        "Response": {
            "title": "Response",
            "type": "object",
            "properties": {
                "I": {"title": "I", "type": "integer"},
                "F": {"title": "F", "type": "number"},
            },
            "required": ["i", "f"],
        },
        "Payload": {
            "title": "Payload",
            "type": "object",
            "properties": {
                "i": {"title": "I", "type": "integer"},
                "f": {"title": "F", "type": "number"},
            },
            "required": ["i", "f"],
        },
    }
    """


def test_schema_list(schema):
    method_list = schema["paths"]["/api/test_list"]["post"]

    assert method_list["requestBody"] == {
        "content": {
            "application/json": {
                "schema": {
                    "items": {"$ref": "#/components/schemas/Payload"},
                    "title": "Data",
                    "type": "array",
                }
            }
        },
        "required": True,
    }
    assert method_list["responses"] == {
        200: {
            "content": {
                "application/json": {
                    "schema": {
                        "items": {"$ref": "#/components/schemas/Response"},
                        "title": "Response",
                        "type": "array",
                    }
                }
            },
            "description": "OK",
        },
        400: PARSE_ERROR_400,
        422: VALIDATION_ERROR_422,
    }

    assert schema["components"]["schemas"] == {
        "Payload": {
            "properties": {
                "f": {"title": "F", "type": "number"},
                "i": {"title": "I", "type": "integer"},
            },
            "required": ["i", "f"],
            "title": "Payload",
            "type": "object",
        },
        "DeprecatedExampleResult": {
            "properties": {
                "f": {"title": "F", "type": "string"},
                "i": {"title": "I", "type": "string"},
            },
            "required": ["i", "f"],
            "title": "DeprecatedExampleResult",
            "type": "object",
        },
        "TypeA": {
            "properties": {
                "a": {"title": "A", "type": "string"},
            },
            "required": ["a"],
            "title": "TypeA",
            "type": "object",
        },
        "TypeB": {
            "properties": {
                "b": {"title": "B", "type": "string"},
            },
            "required": ["b"],
            "title": "TypeB",
            "type": "object",
        },
        "Response": {
            "properties": {
                "f": {"description": "f desc", "title": "f title", "type": "number"},
                "i": {"title": "I", "type": "integer"},
            },
            "required": ["i", "f"],
            "title": "Response",
            "type": "object",
        },
        "ValidationErrorDetail": {
            "additionalProperties": True,
            "properties": {
                "loc": {
                    "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                    "title": "Loc",
                    "type": "array",
                },
                "msg": {"title": "Msg", "type": "string"},
                "type": {"title": "Type", "type": "string"},
            },
            "required": ["loc", "msg", "type"],
            "title": "ValidationErrorDetail",
            "type": "object",
        },
        "ValidationErrorResponse": {
            "properties": {
                "detail": {
                    "items": {"$ref": "#/components/schemas/ValidationErrorDetail"},
                    "title": "Detail",
                    "type": "array",
                }
            },
            "required": ["detail"],
            "title": "ValidationErrorResponse",
            "type": "object",
        },
        "HttpErrorResponse": HTTP_ERROR_RESPONSE,
    }


def test_schema_body(schema):
    method_list = schema["paths"]["/api/test-body"]["post"]

    assert method_list["requestBody"] == {
        "content": {
            "application/json": {
                "schema": {
                    "properties": {
                        "f": {"title": "F", "type": "number"},
                        "i": {"title": "I", "type": "integer"},
                    },
                    "required": ["i", "f"],
                    "title": "BodyParams",
                    "type": "object",
                }
            }
        },
        "required": True,
    }
    assert method_list["responses"] == {
        200: {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Response"}
                }
            },
            "description": "OK",
        },
        400: PARSE_ERROR_400,
        422: VALIDATION_ERROR_422,
    }


def test_schema_body_schema(schema):
    method_list = schema["paths"]["/api/test-body-schema"]["post"]

    assert method_list["requestBody"] == {
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Payload"}},
        },
        "required": True,
    }
    assert method_list["responses"] == {
        200: {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Response"}
                }
            },
            "description": "OK",
        },
        400: PARSE_ERROR_400,
        422: VALIDATION_ERROR_422,
    }


def test_schema_path(schema):
    method_list = schema["paths"]["/api/test-path/{i}/{f}"]["get"]

    assert "requestBody" not in method_list

    assert method_list["parameters"] == [
        {
            "in": "path",
            "name": "i",
            "schema": {"title": "I", "type": "integer"},
            "required": True,
        },
        {
            "in": "path",
            "name": "f",
            "schema": {"title": "F", "type": "number"},
            "required": True,
        },
    ]

    assert method_list["responses"] == {
        200: {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Response"},
                },
            },
            "description": "OK",
        },
        422: VALIDATION_ERROR_422,
    }


def test_schema_pathex(schema):
    method_list = schema["paths"]["/api/test-pathex/{path_ex}"]["get"]

    assert "requestBody" not in method_list

    assert method_list["parameters"] == [
        {
            "in": "path",
            "name": "path_ex",
            "schema": {
                "title": "Path Ex",
                "type": "string",
                "format": "custom-format",
                "description": "path_ex description",
                "example": "example_string",
            },
            "required": True,
            "example": "example_string",
            "description": "path_ex description",
        },
    ]

    assert method_list["responses"] == {
        200: {
            "content": {
                "application/json": {
                    "schema": {
                        "example": "example_string",
                        "format": "custom-format",
                        "title": "Response",
                        "type": "string",
                    },
                },
            },
            "description": "OK",
        },
        422: VALIDATION_ERROR_422,
    }


def test_schema_form(schema):
    method_list = schema["paths"]["/api/test-form"]["post"]

    assert method_list["requestBody"] == {
        "content": {
            "application/x-www-form-urlencoded": {
                "schema": {
                    "title": "FormParams",
                    "type": "object",
                    "properties": {
                        "i": {"title": "I", "type": "integer"},
                        "f": {"title": "F", "type": "number"},
                    },
                    "required": ["i", "f"],
                }
            }
        },
        "required": True,
    }
    assert method_list["responses"] == {
        200: {
            "description": "OK",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Response"}
                }
            },
        },
        400: PARSE_ERROR_400,
        422: VALIDATION_ERROR_422,
    }


def test_schema_single(schema):
    method_list = schema["paths"]["/api/test-form-single"]["post"]

    assert method_list["requestBody"] == {
        "content": {
            "application/x-www-form-urlencoded": {
                "schema": {
                    "properties": {"data": {"title": "Data", "type": "number"}},
                    "required": ["data"],
                    "title": "FormParams",
                    "type": "object",
                }
            }
        },
        "required": True,
    }
    assert method_list["responses"] == {
        200: {
            "description": "OK",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Response"}
                }
            },
        },
        400: PARSE_ERROR_400,
        422: VALIDATION_ERROR_422,
    }


def test_schema_form_body(schema):
    method_list = schema["paths"]["/api/test-form-body"]["post"]

    assert method_list["requestBody"] == {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "properties": {
                        "i": {"default": 10, "title": "I", "type": "integer"},
                        "s": {"default": "10", "title": "S", "type": "string"},
                    },
                    "title": "MultiPartBodyParams",
                    "type": "object",
                }
            }
        },
        "required": False,
    }
    assert method_list["responses"] == {
        200: {
            "description": "OK",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Response"}
                }
            },
        },
        400: PARSE_ERROR_400,
        422: VALIDATION_ERROR_422,
    }


def test_schema_form_file(schema):
    method_list = schema["paths"]["/api/test-form-file"]["post"]

    assert method_list["requestBody"] == {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "properties": {
                        "files": {
                            "items": {"format": "binary", "type": "string"},
                            "title": "Files",
                            "type": "array",
                        },
                        "i": {"title": "I", "type": "integer"},
                        "f": {"title": "F", "type": "number"},
                    },
                    "required": ["files", "i", "f"],
                    "title": "MultiPartBodyParams",
                    "type": "object",
                }
            }
        },
        "required": True,
    }
    assert method_list["responses"] == {
        200: {
            "description": "OK",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Response"}
                }
            },
        },
        400: PARSE_ERROR_400,
        422: VALIDATION_ERROR_422,
    }


def test_schema_body_file(schema):
    method_list = schema["paths"]["/api/test-body-file"]["post"]

    assert method_list["requestBody"] == {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "properties": {
                        "body": {"$ref": "#/components/schemas/Payload"},
                        "files": {
                            "items": {"format": "binary", "type": "string"},
                            "title": "Files",
                            "type": "array",
                        },
                    },
                    "required": ["files", "body"],
                    "title": "MultiPartBodyParams",
                    "type": "object",
                }
            }
        },
        "required": True,
    }
    assert method_list["responses"] == {
        200: {
            "description": "OK",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Response"}
                }
            },
        },
        400: PARSE_ERROR_400,
        422: VALIDATION_ERROR_422,
    }


def test_schema_title_description(schema):
    method_list = schema["paths"]["/api/test-title-description/"]["post"]

    assert method_list["summary"] == "Best API Ever"
    assert method_list["tags"] == ["a-tag"]

    assert method_list["requestBody"] == {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "properties": {
                        "file": {
                            "description": "file param desc",
                            "format": "binary",
                            "title": "File",
                            "type": "string",
                        }
                    },
                    "required": ["file"],
                    "title": "FileParams",
                    "type": "object",
                }
            }
        },
        "required": True,
    }

    assert method_list["parameters"] == [
        {
            "in": "query",
            "name": "param1",
            "required": True,
            "schema": {"title": "param 1 title", "type": "integer"},
        },
        {
            "in": "query",
            "name": "param2",
            "description": "param 2 desc",
            "required": False,
            "schema": {
                "default": "A Default",
                "description": "param 2 desc",
                "title": "Param2",
                "type": "string",
            },
        },
    ]

    assert method_list["responses"] == {
        200: {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Response"}
                }
            },
            "description": "OK",
        },
        400: PARSE_ERROR_400,
        422: VALIDATION_ERROR_422,
    }


def test_schema_deprecated_example_examples(schema):
    method_list = schema["paths"]["/api/test-deprecated-example-examples/"]["post"]

    assert method_list["parameters"] == [
        {
            "deprecated": True,
            "in": "query",
            "name": "param1",
            "required": False,
            "schema": {"title": "Param1", "type": "integer", "deprecated": True},
        },
        {
            "in": "query",
            "name": "param2",
            "required": True,
            "schema": {"title": "Param2", "type": "string", "example": "Example Value"},
            "example": "Example Value",
        },
        {
            "in": "query",
            "name": "param3",
            "required": True,
            "schema": {
                "maxLength": 5,
                "title": "Param3",
                "type": "string",
            },
            "examples": {
                "invalid": {
                    "summary": "Invalid data is rejected with an error",
                    "value": "MoreThan5Length",
                },
                "normal": {
                    "description": "A **normal** string works correctly.",
                    "summary": "A normal example",
                    "value": "Foo",
                },
            },
        },
    ]

    assert method_list["responses"] == {
        200: {
            "description": "OK",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/DeprecatedExampleResult"},
                }
            },
        },
        422: VALIDATION_ERROR_422,
    }


def test_union_payload_type(schema):
    method = schema["paths"]["/api/test-union-type"]["post"]

    assert method["requestBody"] == {
        "content": {
            "application/json": {
                "schema": {
                    "anyOf": [
                        {"$ref": "#/components/schemas/TypeA"},
                        {"$ref": "#/components/schemas/TypeB"},
                    ],
                    "title": "Data",
                }
            }
        },
        "required": True,
    }


def test_union_payload_simple(schema):
    method = schema["paths"]["/api/test-union-type-with-simple"]["post"]

    print(method["requestBody"])
    assert method["requestBody"] == {
        "content": {
            "application/json": {
                "schema": {
                    "title": "Data",
                    "anyOf": [
                        {"type": "integer"},
                        {"$ref": "#/components/schemas/TypeB"},
                    ],
                }
            }
        },
        "required": True,
    }


def test_new_union_payload_type(schema):
    method = schema["paths"]["/api/test-new-union-type"]["post"]

    assert method["requestBody"] == {
        "content": {
            "application/json": {
                "schema": {
                    "anyOf": [
                        {"$ref": "#/components/schemas/TypeA"},
                        {"$ref": "#/components/schemas/TypeB"},
                    ],
                    "title": "Data",
                }
            }
        },
        "required": True,
    }


def test_get_openapi_urls():
    api = HattoriAPI(openapi_url=None)
    paths = get_openapi_urls(api)
    assert len(paths) == 0

    api = HattoriAPI(docs_url=None)
    paths = get_openapi_urls(api)
    assert len(paths) == 1

    api = HattoriAPI(openapi_url="/path", docs_url="/path")
    with pytest.raises(
        AssertionError, match="Please use different urls for openapi_url and docs_url"
    ):
        get_openapi_urls(api)


def test_unique_operation_ids():
    api = HattoriAPI()

    @api.get("/1")
    def same_name(request) -> None:
        pass

    @api.get("/2")  # noqa: F811
    def same_name(request) -> None:  # noqa: F811
        pass

    with pytest.raises(ConfigError, match='Duplicate operation_id "same_name"'):
        api.get_openapi_schema()


def test_duplicate_operation_ids_fail_at_startup():
    api = HattoriAPI()

    @api.get("/1", operation_id="shared")
    def first(request) -> None:
        pass

    @api.get("/2", operation_id="shared")
    def second(request) -> None:
        pass

    # Django imports a URLconf that failed again on the next request, and the
    # API it finds there has to fail again rather than start half-checked.
    for _ in range(2):
        with pytest.raises(
            ConfigError,
            match=r'Duplicate operation_id "shared" \(at .*first and .*second\)',
        ):
            _ = api.urls


def test_method_suffixed_operation_ids_are_checked_at_startup():
    api = HattoriAPI()

    @api.api_operation(["GET", "POST"], "/1")
    def item(request) -> None:
        pass

    @api.get("/2", operation_id="item_post")
    def other(request) -> None:
        pass

    with pytest.raises(ConfigError, match='Duplicate operation_id "item_post"'):
        _ = api.urls


def test_operation_left_out_of_the_schema_may_reuse_an_operation_id():
    api = HattoriAPI()

    @api.get("/1", operation_id="shared")
    def first(request) -> None:
        pass

    @api.get("/2", operation_id="shared", include_in_schema=False)
    def second(request) -> None:
        pass

    assert [
        m["get"]["operationId"] for m in api.get_openapi_schema()["paths"].values()
    ] == ["shared"]


def test_schema_is_built_once():
    api = HattoriAPI()

    @api.get("/ping")
    def ping(request) -> None:
        pass

    client = TestClient(api)
    with patch("hattori.main.get_schema", wraps=get_schema) as build:
        served = client.get("/openapi.json")
        assert client.get("/openapi.json").content == served.content
        assert api.get_openapi_schema() is api.get_openapi_schema()
    assert served.status_code == 200
    assert build.call_count == 1


def test_requests_arriving_together_share_one_build():
    api = HattoriAPI()

    @api.get("/ping")
    def ping(request) -> None:
        pass

    together = threading.Barrier(4)

    def slow_build(**kwargs):
        time.sleep(0.05)
        return get_schema(**kwargs)

    def request(_):
        together.wait()
        return api.get_openapi_schema()

    with patch("hattori.main.get_schema", side_effect=slow_build) as build:
        with ThreadPoolExecutor(4) as pool:
            schemas = list(pool.map(request, range(4)))

    assert build.call_count == 1
    assert all(schema is schemas[0] for schema in schemas)


def test_schema_for_another_mount_point_is_not_rebuilt():
    api = HattoriAPI()
    router = Router()

    @api.get("/ping")
    def ping(request) -> None:
        pass

    @router.post("/ping")
    def create_ping(request) -> None:
        pass

    api.add_router("", router)

    with patch("hattori.main.get_schema", wraps=get_schema) as build:
        schema = api.get_openapi_schema(path_prefix="/a/")
        other = api.get_openapi_schema(path_prefix="/b/")
        assert api.get_openapi_schema(path_prefix="/a/") is schema
    assert build.call_count == 1

    assert list(schema["paths"]) == ["/a/ping"]
    assert list(other["paths"]) == ["/b/ping"]
    assert list(other["paths"]["/b/ping"]) == ["get", "post"]
    assert other["paths"]["/b/ping"]["get"] is schema["paths"]["/a/ping"]["get"]
    assert other["components"] is schema["components"]


def test_operation_id_includes_router_prefix():
    api = HattoriAPI()

    @api.get("/list")
    def list(request) -> None:
        pass

    users = Router()

    @users.get("/")
    def list(request) -> None:  # noqa: F811
        pass

    orders = Router()

    @orders.post("/{id}/items")
    def list(request, id: int) -> None:  # noqa: F811
        pass

    api.add_router("/users", users)
    api.add_router("/orders/{id}/sub", orders)

    schema = api.get_openapi_schema()
    op_ids = {
        path: next(iter(methods.values()))["operationId"]
        for path, methods in schema["paths"].items()
    }
    assert op_ids == {
        "/api/list": "list",
        "/api/users/": "users_list",
        "/api/orders/{id}/sub/{id}/items": "orders_sub_list",
    }


def test_docs_decorator():
    api = HattoriAPI(docs_decorator=staff_member_required)

    paths = get_openapi_urls(api)
    assert len(paths) == 2
    for ptrn in paths:
        request = Mock(user=Mock(is_staff=True))
        result = ptrn.callback(request)
        assert result.status_code == 200

        request = Mock(user=Mock(is_staff=False))
        request.build_absolute_uri = lambda: "http://example.com"
        result = ptrn.callback(request)
        assert result.status_code == 302


class TestRenderer(JSONRenderer):
    media_type = "custom/type"


def test_renderer_media_type():
    api = HattoriAPI(renderer=TestRenderer)

    @api.get("/1")
    def same_name(
        request,
    ) -> TypeA:
        pass

    schema = api.get_openapi_schema()
    method = schema["paths"]["/api/1"]["get"]
    assert method["responses"] == {
        200: {
            "content": {
                "custom/type": {"schema": {"$ref": "#/components/schemas/TypeA"}}
            },
            "description": "OK",
        }
    }


def test_all_paths_rendered():
    api = HattoriAPI(renderer=TestRenderer)

    @api.post("/1")
    def some_name_create(
        request,
    ) -> None:
        pass

    @api.get("/1")
    def some_name_list(
        request,
    ) -> None:
        pass

    @api.get("/1/{param}")
    def some_name_get_one(request, param: int) -> None:
        pass

    @api.delete("/1/{param}")
    def some_name_delete(request, param: int) -> None:
        pass

    schema = api.get_openapi_schema()

    expected_result = {"/api/1": ["post", "get"], "/api/1/{param}": ["get", "delete"]}
    result = {p: list(schema["paths"][p].keys()) for p in schema["paths"].keys()}
    assert expected_result == result


def test_all_paths_typed_params_rendered():
    api = HattoriAPI(renderer=TestRenderer)

    @api.post("/1")
    def some_name_create(
        request,
    ) -> None:
        pass

    @api.get("/1")
    def some_name_list(
        request,
    ) -> None:
        pass

    @api.get("/1/{int:param}")
    def some_name_get_one(request, param: int) -> None:
        pass

    @api.delete("/1/{str:param}")
    def some_name_delete(request, param: str) -> None:
        pass

    schema = api.get_openapi_schema()

    expected_result = {"/api/1": ["post", "get"], "/api/1/{param}": ["get", "delete"]}
    result = {p: list(schema["paths"][p].keys()) for p in schema["paths"].keys()}
    assert expected_result == result


def test_by_alias_uses_serialization_alias_simple():
    """Test the serialization_alias on the Field is used when by_alias=True is set on the route"""
    api = HattoriAPI()

    class PersonOut(Schema):
        uuid: str = Field(..., serialization_alias="id")
        name: str = Field(..., serialization_alias="fullName")

    @api.get("/person", by_alias=True)
    def get_user(request) -> PersonOut:
        return {"uuid": uuid4(), "fullname": "John Snow"}

    schema = api.get_openapi_schema()
    user_alias_schema = schema["components"]["schemas"]["PersonOut"]
    assert user_alias_schema == {
        "title": "PersonOut",
        "type": "object",
        "properties": {
            "id": {"title": "Id", "type": "string"},
            "fullName": {"type": "string", "title": "Fullname"},
        },
        "required": ["id", "fullName"],
    }


def test_by_alias_uses_validation_alias_simple():
    """Test the serialization_alias on the Field is used when by_alias=True is set on the route"""
    api = HattoriAPI()

    class PersonIn(Schema):
        uuid: str = Field(..., validation_alias="id")
        name: str = Field(..., validation_alias="fullName")

    @api.get("/person", by_alias=True)
    def get_user(request, param: PersonIn) -> PersonResult:
        return {"uuid": uuid4(), "fullname": "John Snow"}

    schema = api.get_openapi_schema()
    user_alias_schema = schema["components"]["schemas"]["PersonIn"]
    assert user_alias_schema == {
        "title": "PersonIn",
        "type": "object",
        "properties": {
            "id": {"title": "Id", "type": "string"},
            "fullName": {"type": "string", "title": "Fullname"},
        },
        "required": ["id", "fullName"],
    }


@pytest.mark.django_db
def test_by_alias_uses_serialization_alias_model():
    """Test the serialization_alias on the Field is used when by_alias=True is set on the route"""
    api = HattoriAPI()

    from datetime import datetime

    class PersonModelOut(Schema):
        uuid: str = Field(..., serialization_alias="id")
        created: datetime

    @api.get("/person", by_alias=True)
    def get_user(request) -> PersonModelOut:
        return {"uuid": "abc", "created": "2024-01-01T00:00:00"}

    schema = api.get_openapi_schema()
    user_alias_schema = schema["components"]["schemas"]["PersonModelOut"]
    assert user_alias_schema == {
        "title": "PersonModelOut",
        "type": "object",
        "properties": {
            "id": {"title": "Id", "type": "string"},
            "created": {"type": "string", "format": "date-time", "title": "Created"},
        },
        "required": ["id", "created"],
    }


def test_same_response_model_with_and_without_alias_get_distinct_schema_refs():
    api = HattoriAPI()

    class AliasedPerson(Schema):
        uuid: str = Field(..., serialization_alias="id")

    @api.get("/plain-person")
    def plain_person(request) -> AliasedPerson:
        return {"uuid": "abc"}

    @api.get("/aliased-person", by_alias=True)
    def aliased_person(request) -> AliasedPerson:
        return {"uuid": "abc"}

    schema = api.get_openapi_schema()
    plain_ref = schema["paths"]["/api/plain-person"]["get"]["responses"][200][
        "content"
    ]["application/json"]["schema"]["$ref"]
    aliased_ref = schema["paths"]["/api/aliased-person"]["get"]["responses"][200][
        "content"
    ]["application/json"]["schema"]["$ref"]

    assert plain_ref != aliased_ref
    assert schema["components"]["schemas"][plain_ref.rsplit("/", 1)[-1]][
        "properties"
    ] == {"uuid": {"title": "Uuid", "type": "string"}}
    assert schema["components"]["schemas"][aliased_ref.rsplit("/", 1)[-1]][
        "properties"
    ] == {"id": {"title": "Id", "type": "string"}}


def test_nested_models_with_and_without_alias_keep_inner_refs_consistent():
    api = HattoriAPI()

    class Inner(Schema):
        inner_id: str = Field(..., serialization_alias="iid")

    class Outer(Schema):
        outer_id: str = Field(..., serialization_alias="oid")
        inner: Inner

    @api.get("/plain")
    def plain(request) -> Outer:
        return {"outer_id": "o", "inner": {"inner_id": "i"}}

    @api.get("/aliased", by_alias=True)
    def aliased(request) -> Outer:
        return {"outer_id": "o", "inner": {"inner_id": "i"}}

    schemas = api.get_openapi_schema()["components"]["schemas"]
    plain_outer_ref = api.get_openapi_schema()["paths"]["/api/plain"]["get"][
        "responses"
    ][200]["content"]["application/json"]["schema"]["$ref"]
    aliased_outer_ref = api.get_openapi_schema()["paths"]["/api/aliased"]["get"][
        "responses"
    ][200]["content"]["application/json"]["schema"]["$ref"]

    plain_outer = schemas[plain_outer_ref.rsplit("/", 1)[-1]]
    aliased_outer = schemas[aliased_outer_ref.rsplit("/", 1)[-1]]

    plain_inner = schemas[plain_outer["properties"]["inner"]["$ref"].rsplit("/", 1)[-1]]
    aliased_inner = schemas[
        aliased_outer["properties"]["inner"]["$ref"].rsplit("/", 1)[-1]
    ]

    assert "inner_id" in plain_inner["properties"]
    assert "iid" in aliased_inner["properties"]
    assert "outer_id" in plain_outer["properties"]
    assert "oid" in aliased_outer["properties"]


def test_422_auto_documented():
    api = HattoriAPI()

    @api.get("/items")
    def get_items(request, q: str = Query(...)) -> TypeA:
        return {"a": q}

    schema = api.get_openapi_schema()
    method = schema["paths"]["/api/items"]["get"]
    assert 422 in method["responses"]
    resp_422 = method["responses"][422]
    assert resp_422["description"] == "Unprocessable Content"
    assert resp_422["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ValidationErrorResponse"
    }
    assert "ValidationErrorResponse" in schema["components"]["schemas"]
    assert "ValidationErrorDetail" in schema["components"]["schemas"]


def test_422_not_on_parameterless():
    api = HattoriAPI()

    @api.get("/ping")
    def ping(request) -> TypeA:
        return {"a": "pong"}

    schema = api.get_openapi_schema()
    method = schema["paths"]["/api/ping"]["get"]
    assert 422 not in method["responses"]


def test_explicit_422_is_combined_with_validation_errors():
    api = HattoriAPI()

    class CustomError(Schema):
        error: str

    class ValidationFailed(APIReturn[CustomError]):
        code = 422

    @api.get("/items")
    def get_items(request, q: str = Query(...)) -> TypeA | ValidationFailed:
        return {"a": q}

    schema = api.get_openapi_schema()
    method = schema["paths"]["/api/items"]["get"]
    assert 422 in method["responses"]
    resp_422 = method["responses"][422]
    assert resp_422["content"]["application/json"]["schema"] == {
        "anyOf": [
            {"$ref": "#/components/schemas/CustomError"},
            {"$ref": "#/components/schemas/ValidationErrorResponse"},
        ]
    }
