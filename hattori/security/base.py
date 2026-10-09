import functools
import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from typing import Any

from django.http import HttpRequest

from hattori.errors import ConfigError
from hattori.returns import (
    DeclaredResponses,
    declared_responses,
    return_annotation_arms,
)
from hattori.utils import is_async_callable

__all__ = [
    "SecuritySchema",
    "AuthBase",
    "auth_attribute",
    "auth_can_decline",
    "auth_declaration",
    "auth_layers",
    "declared_auth_responses",
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
    whose auths may all decline — beside any ``APIReturn`` variants declared
    for the same code.

    An auth that answers every request itself, with a principal or with a
    response of its own, says so by setting :attr:`can_decline` to ``False``.
    """

    #: Whether this auth may decline a request by returning a falsy value. Set
    #: it to ``False`` on an auth that never does: the framework's own ``401``
    #: is then left out of the spec for the operations it guards, and a falsy
    #: result from it is a :class:`~hattori.errors.ConfigError`. Not for an
    #: auth that declines by itself when the request carries no credentials, as
    #: ``HttpBearer`` and ``HttpBasicAuth`` do.
    can_decline: bool = True

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

        # Only the APIReturn arms of the annotation contribute. No annotation
        # means no typed auth entries in the OpenAPI spec.
        arms = return_annotation_arms(_auth_target(self))
        declared = declared_responses(arms or (), f"{type(self).__name__}.authenticate")
        self.auth_responses: dict[int, Any] = declared.schemas
        self.auth_descriptions: dict[int, list[str]] = declared.descriptions

    @abstractmethod
    def __call__(self, request: HttpRequest) -> Any | None:
        pass  # pragma: no cover


def _auth_target(auth: AuthBase) -> Callable[..., Any]:
    """``authenticate``, or ``__call__`` for auth that skips that convention."""
    target: Callable[..., Any] | None = getattr(auth, "authenticate", None)
    return auth.__call__ if target is None else target


def auth_layers(callback: Any) -> Iterator[Any]:
    """``callback``, then each callable it wraps, outermost first.

    Follows ``functools.partial`` and the ``__wrapped__`` that ``functools.wraps``
    leaves, so wrapping an auth callback hides nothing the spec reads off it.
    """
    seen: set[int] = set()
    while callable(callback) and id(callback) not in seen:
        seen.add(id(callback))
        yield callback
        if isinstance(callback, functools.partial):
            callback = callback.func
        else:
            callback = getattr(callback, "__wrapped__", None)


_UNSET: Any = object()


def auth_declaration(callback: Any, name: str) -> tuple[Any, Any]:
    """The outermost layer of ``callback`` that sets ``name``, and what it sets.

    What a wrapper declares for itself comes before what it wraps, ``None``
    included. ``(None, None)`` when no layer sets it.
    """
    for layer in auth_layers(callback):
        value = getattr(layer, name, _UNSET)
        if value is not _UNSET:
            return layer, value
    return None, None


def auth_attribute(callback: Any, name: str) -> Any:
    """``name`` as the outermost layer of ``callback`` that sets it has it."""
    return auth_declaration(callback, name)[1]


def auth_can_decline(callback: Any) -> bool:
    """Whether an auth callback may decline a request by returning a falsy value.

    Any callback may, unless it says otherwise: ``can_decline = False``, on an
    auth class or on a plain function, as the outermost layer that sets it has
    it. So a wrapper that declines for itself says ``True`` over what it wraps.
    """
    layer, declared = auth_declaration(callback, "can_decline")
    if layer is None:
        return True
    if not isinstance(declared, bool):
        owner = getattr(layer, "__qualname__", type(layer).__name__)
        raise ConfigError(
            f"{owner}.can_decline must be True or False, got {declared!r}."
        )
    return declared


def declared_auth_responses(callback: Any) -> DeclaredResponses:
    """The typed responses an auth callback declares, and their descriptions.

    An :class:`AuthBase` read them off ``authenticate`` when it was created. Any
    other callable declares them the same way, on its own return annotation. A
    layer that lists ``auth_responses`` itself is taken at its word, with the
    ``auth_descriptions`` it lists beside them, if any.
    """
    for layer in auth_layers(callback):
        listed: dict[int, Any] | None = getattr(layer, "auth_responses", _UNSET)
        if listed is not _UNSET:
            described = getattr(layer, "auth_descriptions", None)
            return DeclaredResponses(listed or {}, described or {})
        if isinstance(layer, functools.partial):
            continue
        target = layer if inspect.isroutine(layer) else layer.__call__
        arms = return_annotation_arms(target)
        if arms is not None:
            owner = getattr(layer, "__qualname__", type(layer).__name__)
            return declared_responses(arms, owner)
    return DeclaredResponses()
