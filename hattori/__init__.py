"""Django Hattori - Fast Django REST framework"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("django-hattori")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"


from pydantic import Field

from hattori.errors import (
    ApiError,
    ErrorBody,
    HttpErrorBody,
    ValidationErrorBody,
    get_http_error_model,
    get_validation_error_model,
    set_http_error_model,
    set_validation_error_model,
)
from hattori.files import UploadedFile
from hattori.filter_schema import FilterConfigDict, FilterLookup, FilterSchema
from hattori.http_errors import (
    BadGateway,
    BadRequest,
    Conflict,
    EnumT,
    Forbidden,
    GatewayTimeout,
    Gone,
    HTTPError,
    InternalServerError,
    MethodNotAllowed,
    NotFound,
    PayloadTooLarge,
    PaymentRequired,
    ServiceUnavailable,
    TooManyRequests,
    Unauthorized,
    UnprocessableEntity,
    get_default_error_body,
    set_default_error_body,
)
from hattori.main import HattoriAPI
from hattori.openapi.docs import Redoc, Swagger
from hattori.params import (
    Body,
    BodyEx,
    Cookie,
    CookieEx,
    File,
    FileEx,
    Form,
    FormEx,
    Header,
    HeaderEx,
    P,
    Path,
    PathEx,
    Query,
    QueryEx,
)
from hattori.patch_dict import PatchDict, PatchName
from hattori.responses import Accepted, APIReturn, Created, NoContent
from hattori.router import Router
from hattori.schema import Schema
from hattori.security import BasePermission
from hattori.streaming import JSONL, SSE
from hattori.types import AuthedRequest

__all__ = [
    "Field",
    "UploadedFile",
    "HattoriAPI",
    "AuthedRequest",
    "Body",
    "Cookie",
    "File",
    "Form",
    "Header",
    "Path",
    "Query",
    "BodyEx",
    "CookieEx",
    "FileEx",
    "FormEx",
    "HeaderEx",
    "PathEx",
    "QueryEx",
    "Router",
    "P",
    "Schema",
    "BasePermission",
    "FilterSchema",
    "FilterLookup",
    "FilterConfigDict",
    "Swagger",
    "Redoc",
    "PatchDict",
    "PatchName",
    "SSE",
    "JSONL",
    "APIReturn",
    "Created",
    "Accepted",
    "NoContent",
    "ApiError",
    "ErrorBody",
    "HttpErrorBody",
    "ValidationErrorBody",
    "EnumT",
    "HTTPError",
    "BadRequest",
    "Unauthorized",
    "PaymentRequired",
    "Forbidden",
    "NotFound",
    "MethodNotAllowed",
    "Conflict",
    "Gone",
    "PayloadTooLarge",
    "UnprocessableEntity",
    "TooManyRequests",
    "InternalServerError",
    "BadGateway",
    "ServiceUnavailable",
    "GatewayTimeout",
    "set_default_error_body",
    "get_default_error_body",
    "set_validation_error_model",
    "get_validation_error_model",
    "set_http_error_model",
    "get_http_error_model",
]
