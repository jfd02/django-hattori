from dataclasses import fields, is_dataclass
from datetime import timedelta
from decimal import Decimal
from functools import partial
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Any, ClassVar, Generic, TypeVar, get_args, get_origin

import orjson
from django.http import HttpResponse
from django.utils.duration import duration_iso_string
from django.utils.functional import Promise
from pydantic import AnyUrl, BaseModel
from pydantic_core import Url

from hattori.schema import pydantic_version

__all__ = [
    "APIReturn",
    "Created",
    "Accepted",
    "NoContent",
    "resolve_api_return_schema",
    "JsonResponse",
    "dump_model",
    "json_default",
    "json_dumps",
    "json_loads",
    "JSON_OPT",
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

    @classmethod
    def body_schema(cls) -> Any:
        """The body type this response carries: the ``T`` of ``APIReturn[T]``.

        Read off the class's bases, however far up they declare it:
        ``class UserNotFound(AppError)`` where ``AppError`` is
        ``APIReturn[ErrorBody]`` carries an ``ErrorBody``.

        A class whose generic parameter is not its body overrides this to say
        what its body is, as :class:`hattori.ApiError` does.
        """
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


def resolve_api_return_schema(cls: type[APIReturn[Any]]) -> Any:
    """The body type of an ``APIReturn`` subclass: :meth:`APIReturn.body_schema`."""
    return cls.body_schema()


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


def dump_model(model: BaseModel, mode: str, **options: Any) -> Any:
    """``model.model_dump`` in the renderer's ``mode``, with its ``options``.

    In JSON mode what pydantic cannot encode goes to :func:`json_default`.
    """
    dump = model.model_dump
    if mode == "json":
        if pydantic_version < [2, 11]:
            # Older model_dump versions do not expose fallback.
            dump = partial(model.__pydantic_serializer__.to_python, model)
        options["fallback"] = json_default
    return dump(mode=mode, **options)


def json_dumps(data: Any) -> bytes:
    try:
        return orjson.dumps(data, default=json_default, option=JSON_OPT)
    except TypeError as exc:
        if str(exc) not in {
            "Integer exceeds 64-bit range",
            "Dict integer key must be within 64-bit range",
        }:
            raise
        # JSON Schema integers and Python ints have no 64-bit limit. Retain
        # orjson's encoding for other values, using raw JSON only for large ints.
        return orjson.dumps(
            _preserve_large_integers(data),
            default=lambda obj: _preserve_large_integers(json_default(obj)),
            option=JSON_OPT,
        )


def _preserve_large_integers(data: Any) -> Any:
    if isinstance(data, int) and not -(2**63) <= data < 2**64:
        return orjson.Fragment(str(int(data)).encode())
    if isinstance(data, dict):
        return {
            str(int(key))
            if isinstance(key, int) and not -(2**63) <= key < 2**64
            else key: _preserve_large_integers(value)
            for key, value in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [_preserve_large_integers(value) for value in data]
    if is_dataclass(data) and not isinstance(data, type):
        return {
            field.name: _preserve_large_integers(getattr(data, field.name))
            for field in fields(data)
            if not field.name.startswith("_")
        }
    return data


def json_loads(data: Any) -> Any:
    return orjson.loads(data)


class JsonResponse(HttpResponse):
    def __init__(self, data: Any, **kwargs: Any) -> None:
        kwargs.setdefault("content_type", "application/json")
        super().__init__(content=json_dumps(data), **kwargs)
