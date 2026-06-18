"""Django Hattori - Fast Django REST framework"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("django-hattori")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"


from pydantic import Field

from hattori.errors import ApiError, ErrorBody
from hattori.files import UploadedFile
from hattori.filter_schema import FilterConfigDict, FilterLookup, FilterSchema
from hattori.http_errors import (
    BadRequest,
    Conflict,
    Forbidden,
    Gone,
    HTTPError,
    InternalServerError,
    MethodNotAllowed,
    NotFound,
    PayloadTooLarge,
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
from hattori.patch_dict import PatchDict
from hattori.responses import Accepted, APIReturn, Created, NoContent
from hattori.router import Router
from hattori.schema import Schema
from hattori.streaming import JSONL, SSE

__all__ = [
    "Field",
    "UploadedFile",
    "HattoriAPI",
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
    "FilterSchema",
    "FilterLookup",
    "FilterConfigDict",
    "Swagger",
    "Redoc",
    "PatchDict",
    "SSE",
    "JSONL",
    "APIReturn",
    "Created",
    "Accepted",
    "NoContent",
    "ApiError",
    "ErrorBody",
    "HTTPError",
    "BadRequest",
    "Unauthorized",
    "Forbidden",
    "NotFound",
    "MethodNotAllowed",
    "Conflict",
    "Gone",
    "PayloadTooLarge",
    "UnprocessableEntity",
    "TooManyRequests",
    "InternalServerError",
    "set_default_error_body",
    "get_default_error_body",
]
