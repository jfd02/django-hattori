from datetime import timedelta
from decimal import Decimal
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Any, ClassVar, Generic, TypeVar, get_args, get_origin

import orjson
from django.http import HttpResponse
from django.utils.duration import duration_iso_string
from django.utils.functional import Promise
from pydantic import AnyUrl, BaseModel
from pydantic_core import Url

__all__ = [
    "APIReturn",
    "Created",
    "Accepted",
    "NoContent",
    "resolve_api_return_schema",
    "JsonResponse",
    "json_default",
    "json_dumps",
    "json_loads",
    "JSON_OPT",
    "codes_1xx",
    "codes_2xx",
    "codes_3xx",
    "codes_4xx",
    "codes_5xx",
]

JSON_OPT = orjson.OPT_UTC_Z | orjson.OPT_NON_STR_KEYS

T = TypeVar("T")


class APIReturn(Generic[T]):
    """Typed API response with status code pinned on the subclass.

    Subclass to bind a status code (and optional description) to a payload type::

        class UserNotFound(APIReturn[ErrorBody]):
            code = 404
            description = "User with given id does not exist"

        @api.get("/users/{id}")
        def get_user(request, id: int) -> UserOut | UserNotFound:
            if not found:
                return UserNotFound(ErrorBody(message="nope"))
            return user                       # bare type = implicit 200

    Bare (non-``APIReturn``) return types in the annotation implicitly map to 200.
    """

    code: ClassVar[int]
    description: ClassVar[str] = ""

    __slots__ = ("value",)

    def __init__(self, value: T) -> None:
        self.value = value


def resolve_api_return_schema(cls: type) -> Any:
    """Walk the MRO of an APIReturn subclass and return the resolved ``T`` it was
    parameterized with (e.g. ``APIReturn[ErrorBody]`` → ``ErrorBody``).

    Handles multi-level inheritance: ``class UserNotFound(AppError)`` where
    ``AppError`` is ``APIReturn[ErrorBody]`` resolves ``UserNotFound`` → ``ErrorBody``.

    A subclass may pin the response body explicitly by setting
    ``__hattori_response_body__``; this short-circuits the MRO walk and is used
    by :class:`hattori.HTTPError`, whose generic parameter is metadata
    (an enum member) rather than the body type.
    """
    explicit = getattr(cls, "__hattori_response_body__", None)
    if explicit is not None:
        return explicit
    for klass in cls.__mro__:
        for base in getattr(klass, "__orig_bases__", ()):
            origin = get_origin(base)
            if origin is None:
                continue
            try:
                is_api_return = isinstance(origin, type) and issubclass(
                    origin, APIReturn
                )
            except TypeError:
                is_api_return = False
            if not is_api_return:
                continue
            args = get_args(base)
            if args and not isinstance(args[0], TypeVar):
                return args[0]
    raise ValueError(
        f"{cls.__name__} must parameterize APIReturn with a schema type, "
        f"e.g. `class {cls.__name__}(APIReturn[MyModel])`."
    )


class Created(APIReturn[T]):
    """201 Created. Use as ``Created[BodyType](body)`` in return positions::

        def create(...) -> Created[UserOut] | DuplicateName:
            return Created(user)
    """
    code: ClassVar[int] = 201


class Accepted(APIReturn[T]):
    """202 Accepted. Use as ``Accepted[BodyType](body)`` for async/queued work."""
    code: ClassVar[int] = 202


class NoContent(APIReturn[None]):
    """204 No Content. No body. Construct with no args::

        def delete(...) -> NoContent | NotFound:
            return NoContent()
    """
    code: ClassVar[int] = 204

    def __init__(self) -> None:
        super().__init__(None)


def json_default(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, (Url, AnyUrl)):
        return str(obj)
    if isinstance(obj, (IPv4Address, IPv4Network, IPv6Address, IPv6Network)):
        return str(obj)
    if isinstance(obj, timedelta):
        return duration_iso_string(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Promise):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def json_dumps(data: Any) -> bytes:
    return orjson.dumps(data, default=json_default, option=JSON_OPT)


def json_loads(data: Any) -> Any:
    return orjson.loads(data)


class JsonResponse(HttpResponse):
    def __init__(self, data: Any, **kwargs: Any) -> None:
        kwargs.setdefault("content_type", "application/json")
        super().__init__(content=json_dumps(data), **kwargs)


def resp_codes(from_code: int, to_code: int) -> frozenset[int]:
    return frozenset(range(from_code, to_code + 1))


# most common http status codes
codes_1xx = resp_codes(100, 101)
codes_2xx = resp_codes(200, 206)
codes_3xx = resp_codes(300, 308)
codes_4xx = resp_codes(400, 412) | frozenset({416, 418, 425, 429, 451})
codes_5xx = resp_codes(500, 504)
