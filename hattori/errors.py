import logging
import traceback
from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Literal, TypeVar

import pydantic
from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse

from hattori.responses import APIReturn

if TYPE_CHECKING:
    from hattori import HattoriAPI  # pragma: no cover
    from hattori.params.models import ParamModel  # pragma: no cover

__all__ = [
    "ConfigError",
    "AuthenticationError",
    "AuthorizationError",
    "ValidationError",
    "ValidationErrorDetail",
    "ValidationErrorResponse",
    "HttpError",
    "ErrorBody",
    "ApiError",
    "set_default_error_body",
    "get_default_error_body",
    "set_default_exc_handlers",
]


logger = logging.getLogger("django")


class ConfigError(Exception):
    pass


TModel = TypeVar("TModel", bound="ParamModel")


class ValidationErrorContext(Generic[TModel]):
    """
    The full context of a `pydantic.ValidationError`, including all information
    needed to produce a `hattori.errors.ValidationError`.
    """

    def __init__(
        self, pydantic_validation_error: pydantic.ValidationError, model: TModel
    ):
        self.pydantic_validation_error = pydantic_validation_error
        self.model = model


class ValidationError(Exception):
    """
    This exception raised when operation params do not validate
    Note: this is not the same as pydantic.ValidationError
    the errors attribute as well holds the location of the error(body, form, query, etc.)
    """

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(errors)


class HttpError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(status_code, message)

    def __str__(self) -> str:
        return self.message


class AuthenticationError(HttpError):
    def __init__(self, status_code: int = 401, message: str = "Unauthorized") -> None:
        super().__init__(status_code=status_code, message=message)


class AuthorizationError(HttpError):
    def __init__(self, status_code: int = 403, message: str = "Forbidden") -> None:
        super().__init__(status_code=status_code, message=message)


class ValidationErrorDetail(pydantic.BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ValidationErrorResponse(pydantic.BaseModel):
    detail: list[ValidationErrorDetail]


class ErrorBody(pydantic.BaseModel):
    """Default error response body shipped with hattori: ``{code, message}``.

    Use with :class:`ApiError` for a zero-boilerplate error pattern. If your API
    uses a different shape, subclass :class:`~hattori.APIReturn` directly with
    your own body type instead.
    """

    code: str
    message: str


_default_error_body: type[ErrorBody] = ErrorBody


def set_default_error_body(body: type[ErrorBody]) -> None:
    """Set the project-wide default body shape for error responses.

    Used as the final fallback when an :class:`ApiError` (or
    :class:`~hattori.HTTPError`) subclass — and none of its parents — declares
    its own body via the ``body=`` class kwarg. Call once at startup, e.g. from
    an ``AppConfig.ready()`` hook.
    """
    global _default_error_body
    _default_error_body = body


def get_default_error_body() -> type[ErrorBody]:
    return _default_error_body


def resolve_error_body_base(cls: type) -> type[ErrorBody]:
    """The body shape ``cls`` builds on: nearest ``body=`` kwarg, else the default."""
    for klass in cls.__mro__:
        base: type[ErrorBody] | None = klass.__dict__.get(
            "__hattori_response_body_base__"
        )
        if base is not None:
            return base
    return _default_error_body


class ApiError(APIReturn[ErrorBody]):
    """Default error-response base.

    Subclass with a concrete ``code``, ``error_code``, and (optionally) a static
    ``message``::

        class UserNotFound(ApiError):
            code = 404
            error_code = "user_not_found"
            message = "No user with that id"

        @api.get("/users/{id}")
        def get_user(request, id: int) -> UserOut | UserNotFound:
            if not found:
                return UserNotFound()              # uses static message
            return user

    Override the message per call when it needs to be dynamic::

        return UserNotFound(f"No user with id {id}")

    Every subclass that declares an ``error_code`` gets a body model of its own
    whose ``code`` is narrowed to ``Literal["<error_code>"]``, so a generated
    client can discriminate on it. To extend the body with extra fields, pass a
    ``body=`` base::

        class RateLimited(ApiError, body=RetryableErrorBody):
            code = 429
            error_code = "rate_limited"

        return RateLimited("Slow down", retry_after=30)

    To use a completely different error body shape, skip ``ApiError`` and
    subclass :class:`~hattori.APIReturn` directly::

        class MyError(APIReturn[MyErrorShape]):
            code: ClassVar[int]
            def __init__(self, ...): ...
    """

    code: ClassVar[int]
    error_code: ClassVar[str]
    message: ClassVar[str] = ""

    # Pinned so the schema resolver short-circuits its MRO walk. Subclasses that
    # declare an ``error_code`` replace this with a generated ErrorBody subclass
    # whose ``code`` field is narrowed to that code.
    __hattori_response_body__ = ErrorBody
    __hattori_response_body_base__: ClassVar[type[ErrorBody] | None] = None

    def __init_subclass__(
        cls, *, body: type[ErrorBody] | None = None, **kwargs: Any
    ) -> None:
        super().__init_subclass__(**kwargs)
        if body is not None:
            cls.__hattori_response_body_base__ = body
        # Only classes that declare their own ``error_code`` get a narrowed
        # body. An abstract intermediate that just pins ``code`` keeps whatever
        # its parent resolved to, and HTTPError subclasses are left alone —
        # HTTPError assigns ``error_code`` from its enum member *after* this
        # runs, and synthesizes the narrowed body itself.
        error_code = cls.__dict__.get("error_code")
        if isinstance(error_code, str):
            cls.__hattori_response_body__ = pydantic.create_model(
                cls.__name__,
                __base__=resolve_error_body_base(cls),
                __module__=cls.__module__,
                code=(Literal[error_code], ...),
            )

    def __init__(self, message: str | None = None, **body_fields: Any) -> None:
        body_type = self.__hattori_response_body__
        super().__init__(
            body_type(
                code=self.error_code,
                message=message if message is not None else self.message,
                **body_fields,
            )
        )


def set_default_exc_handlers(api: HattoriAPI) -> None:
    api.add_exception_handler(
        Exception,
        partial(_default_exception, api=api),
    )
    api.add_exception_handler(
        Http404,
        partial(_default_404, api=api),
    )
    api.add_exception_handler(
        HttpError,
        partial(_default_http_error, api=api),
    )
    api.add_exception_handler(
        ValidationError,
        partial(_default_validation_error, api=api),
    )


def _default_404(request: HttpRequest, exc: Exception, api: HattoriAPI) -> HttpResponse:
    msg = "Not Found"
    if settings.DEBUG:
        msg += f": {exc}"
    return api.create_response(request, {"detail": msg}, status=404)


def _default_http_error(
    request: HttpRequest, exc: HttpError, api: HattoriAPI
) -> HttpResponse:
    return api.create_response(request, {"detail": str(exc)}, status=exc.status_code)


def _default_validation_error(
    request: HttpRequest, exc: ValidationError, api: HattoriAPI
) -> HttpResponse:
    return api.create_response(request, {"detail": exc.errors}, status=422)


def _default_exception(
    request: HttpRequest, exc: Exception, api: HattoriAPI
) -> HttpResponse:
    if not settings.DEBUG:
        raise exc  # let django deal with it

    logger.exception(exc)
    tb = traceback.format_exc()
    return HttpResponse(tb, status=500, content_type="text/plain")
