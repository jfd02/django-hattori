from abc import ABC, abstractmethod
from typing import Any, Callable, Union, get_args, get_origin

from django.http import HttpRequest
from typing_extensions import get_type_hints

from hattori.compatibility.util import UNION_TYPES
from hattori.errors import ConfigError
from hattori.responses import APIReturn, resolve_api_return_schema
from hattori.utils import is_async_callable

__all__ = ["SecuritySchema", "AuthBase"]


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

    Auth classes without ``APIReturn`` variants in their return annotation
    contribute nothing to the OpenAPI spec. Raising ``AuthenticationError``
    still returns a 401 at runtime via the global exception handler — it just
    won't be documented on every endpoint's spec entry.
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
            self.is_async = is_async_callable(getattr(self, "authenticate"))

        self.auth_responses: dict[int, Any] = _parse_auth_responses(self)

    @abstractmethod
    def __call__(self, request: HttpRequest) -> Any | None:
        pass  # pragma: no cover


def _parse_auth_responses(auth: AuthBase) -> dict[int, Any]:
    """Extract ``{code: body_schema}`` from ``authenticate``'s return annotation.

    Looks at ``authenticate`` first, falls back to ``__call__`` for custom auth
    classes that skip the ``authenticate`` convention. Only ``APIReturn``
    subclasses in the annotation contribute to the result. No annotation means
    no auth entries in the OpenAPI spec.
    """
    target: Callable[..., Any] | None = getattr(auth, "authenticate", None)
    if target is None:
        target = auth.__call__

    try:
        hints = get_type_hints(target)
    except Exception:
        return {}

    annotation = hints.get("return")
    if annotation is None:
        return {}

    origin = get_origin(annotation)
    arms = get_args(annotation) if origin in UNION_TYPES else (annotation,)

    responses: dict[int, Any] = {}
    for arm in arms:
        if not (isinstance(arm, type) and issubclass(arm, APIReturn)):
            continue
        code = getattr(arm, "code", None)
        if not isinstance(code, int):
            raise ConfigError(
                f"{arm.__name__} (in return type of {type(auth).__name__}"
                f".authenticate) must define a concrete `code: ClassVar[int]`."
            )
        try:
            schema = resolve_api_return_schema(arm)
        except ValueError as e:
            raise ConfigError(str(e)) from e
        existing = responses.get(code)
        if existing is None or existing is schema:
            responses[code] = schema
        else:
            responses[code] = Union[existing, schema]

    return responses
