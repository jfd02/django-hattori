from datetime import timedelta
from decimal import Decimal
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Any, ClassVar, Generic, TypeVar

import orjson
from django.http import HttpResponse
from django.utils.duration import duration_iso_string
from django.utils.functional import Promise
from pydantic import AnyUrl, BaseModel
from pydantic_core import Url

__all__ = [
    "APIReturn",
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
