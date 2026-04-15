"""Enum-keyed HTTP error responses.

Lets you bind an `ApiError` subclass to a specific enum member statically, so
the wire ``code`` is derived from the member's ``.value`` and pyright can verify
the response class matches the error variant a service returned.

Usage:

    from enum import Enum
    from typing import Literal
    from hattori import Conflict, NotFound

    class CreateError(Enum):
        DUPLICATE_NAME = "duplicate_name"
        GROUP_NOT_FOUND = "group_not_found"

    class DuplicateName(Conflict[Literal[CreateError.DUPLICATE_NAME]]):
        message = "Already exists"

    class GroupNotFound(NotFound[Literal[CreateError.GROUP_NOT_FOUND]]):
        message = "Group not found"

The status code comes from the semantic base (``Conflict`` -> 409,
``NotFound`` -> 404). The wire ``code`` is set automatically from the enum
member's value.
"""

from enum import Enum
from typing import Any, ClassVar, Generic, get_args, get_origin

from typing_extensions import TypeVar

from hattori.errors import ApiError, ErrorBody

__all__ = [
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
]


EnumT = TypeVar("EnumT", bound=Enum)


def _resolve_enum_member(cls: type) -> Enum | None:
    """Walk ``cls`` MRO looking for ``HTTPError[Literal[E.X]]`` and return E.X.

    Returns None if no enum member is bound (e.g. an intermediate abstract
    subclass that hasn't been parameterized yet).
    """
    for klass in cls.__mro__:
        for base in getattr(klass, "__orig_bases__", ()):
            origin = get_origin(base)
            if origin is None or not isinstance(origin, type):
                continue
            try:
                is_http_error_base = issubclass(origin, HTTPError)
            except TypeError:
                is_http_error_base = False
            if not is_http_error_base:
                continue
            for arg in get_args(base):
                # Conflict[Literal[E.X]] -> arg is Literal[E.X]; unwrap.
                for literal_arg in get_args(arg):
                    if isinstance(literal_arg, Enum):
                        return literal_arg
                # Tolerate a bare enum member if someone bypassed Literal.
                if isinstance(arg, Enum):
                    return arg
    return None


class HTTPError(ApiError, Generic[EnumT]):
    """Generic error response parameterized by a Literal enum member.

    Don't use directly — subclass via a semantic status base
    (:class:`Conflict`, :class:`NotFound`, etc.) and pass a Literal of an
    enum member. ``error_code`` is set automatically from the member's
    ``.value``.
    """

    # Pin the response body to ErrorBody so the framework's MRO-walking schema
    # resolver doesn't mistake our `Literal[E.X]` parameter for a body type.
    __hattori_response_body__ = ErrorBody

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        member = _resolve_enum_member(cls)
        if member is not None:
            cls.error_code = member.value


class BadRequest(HTTPError[EnumT]):
    code: ClassVar[int] = 400


class Unauthorized(HTTPError[EnumT]):
    code: ClassVar[int] = 401


class Forbidden(HTTPError[EnumT]):
    code: ClassVar[int] = 403


class NotFound(HTTPError[EnumT]):
    code: ClassVar[int] = 404


class MethodNotAllowed(HTTPError[EnumT]):
    code: ClassVar[int] = 405


class Conflict(HTTPError[EnumT]):
    code: ClassVar[int] = 409


class Gone(HTTPError[EnumT]):
    code: ClassVar[int] = 410


class PayloadTooLarge(HTTPError[EnumT]):
    code: ClassVar[int] = 413


class UnprocessableEntity(HTTPError[EnumT]):
    code: ClassVar[int] = 422


class TooManyRequests(HTTPError[EnumT]):
    code: ClassVar[int] = 429


class InternalServerError(HTTPError[EnumT]):
    code: ClassVar[int] = 500
