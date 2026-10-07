from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from typing import Any, TypeAliasType, get_args, get_origin, get_type_hints

from django.http import HttpRequest

from hattori.compatibility.util import UNION_TYPES
from hattori.errors import ConfigError
from hattori.responses import APIReturn, resolve_api_return_schema
from hattori.utils import is_async_callable

__all__ = [
    "SecuritySchema",
    "AuthBase",
    "parse_api_return_responses",
    "return_annotation_arms",
]


class SecuritySchema(dict):
    def __init__(self, type: str, **kwargs: Any) -> None:
        super().__init__(type=type, **kwargs)


class AuthBase(ABC):
    """Base class for authentication.

    Declare the possible outcomes on ``authenticate`` (or on ``__call__`` for
    custom auth classes that don't use ``authenticate``) using a union of the
    auth-result type and any number of :class:`~hattori.APIReturn` subclasses::

        class BearerAuth(HttpBearer):
            def authenticate(
                self, request, token
            ) -> User | BadToken | AccountLocked:
                if invalid(token):     return BadToken()
                if locked(user):       return AccountLocked()
                return user

    Each ``APIReturn`` subclass contributes its ``code`` (and body schema) to
    every operation that uses this auth, both at runtime (returning an instance
    short-circuits to that HTTP response) and in the OpenAPI spec.

    Returning ``None``, or any other falsy value, declines the request. When
    every auth on an operation declines, the framework answers ``401`` with the
    ``HttpError`` body, which the OpenAPI spec documents on every operation
    that has auth — beside any ``APIReturn`` variants declared for the same code.
    """

    def __init__(self) -> None:
        if not hasattr(self, "openapi_type"):
            raise ConfigError("If you extend AuthBase you need to define openapi_type")

        kwargs = {}
        for attr in dir(self):
            if attr.startswith("openapi_"):
                name = attr.replace("openapi_", "", 1)
                kwargs[name] = getattr(self, attr)
        self.openapi_security_schema = SecuritySchema(**kwargs)

        self.is_async = False
        if hasattr(self, "authenticate"):  # pragma: no branch
            self.is_async = is_async_callable(self.authenticate)

        self.auth_responses: dict[int, Any] = _parse_auth_responses(self)

    @abstractmethod
    def __call__(self, request: HttpRequest) -> Any | None:
        pass  # pragma: no cover


def return_annotation_arms(target: Callable[..., Any]) -> tuple[Any, ...] | None:
    """The union arms of ``target``'s return annotation, or ``None`` without one.

    A ``type`` alias counts as the type it names, so the arms behind it are read
    too. One whose value cannot be resolved stays an arm of its own.
    """
    try:
        hints = get_type_hints(target)
    except Exception:
        return None

    annotation = hints.get("return")
    if annotation is None:
        return None
    return tuple(_union_arms(annotation))


def _union_arms(annotation: Any) -> Iterator[Any]:
    if isinstance(annotation, TypeAliasType):
        try:
            value = annotation.__value__
        except Exception:
            # It names something only the type checker can see. The arms beside
            # it are still read.
            yield annotation
            return
        yield from _union_arms(value)
    elif get_origin(annotation) in UNION_TYPES:
        for arm in get_args(annotation):
            yield from _union_arms(arm)
    else:
        yield annotation


def parse_api_return_responses(
    target: Callable[..., Any], owner: str
) -> dict[int, Any]:
    """Extract ``{code: body_schema}`` from a callable's return annotation.

    Walks the union arms of ``target``'s return type and, for every
    :class:`~hattori.APIReturn` subclass found, records its ``code`` and resolved
    body schema. Shared by auth (``authenticate``) and permissions (``check``) so
    both contribute their typed responses to the OpenAPI spec the same way.

    ``owner`` is a human-readable label used in error messages (e.g.
    ``"BearerAuth.authenticate"``). No annotation means an empty result.
    """
    responses: dict[int, Any] = {}
    for arm in return_annotation_arms(target) or ():
        if not (isinstance(arm, type) and issubclass(arm, APIReturn)):
            continue
        code = getattr(arm, "code", None)
        if not isinstance(code, int):
            raise ConfigError(
                f"{arm.__name__} (in return type of {owner}) must define a "
                f"concrete `code: ClassVar[int]`."
            )
        try:
            schema = resolve_api_return_schema(arm)
        except ValueError as e:
            raise ConfigError(str(e)) from e
        existing = responses.get(code)
        if existing is None or existing is schema:
            responses[code] = schema
        else:
            responses[code] = existing | schema

    return responses


def _parse_auth_responses(auth: AuthBase) -> dict[int, Any]:
    """Extract ``{code: body_schema}`` from ``authenticate``'s return annotation.

    Looks at ``authenticate`` first, falls back to ``__call__`` for custom auth
    classes that skip the ``authenticate`` convention. Only ``APIReturn``
    subclasses in the annotation contribute to the result. No annotation means
    no typed auth entries in the OpenAPI spec.
    """
    target: Callable[..., Any] | None = getattr(auth, "authenticate", None)
    if target is None:
        target = auth.__call__
    return parse_api_return_responses(target, f"{type(auth).__name__}.authenticate")
