import logging
import traceback
from copy import deepcopy
from dataclasses import replace
from functools import partial
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Generic,
    Literal,
    Self,
    TypeVar,
)

import pydantic
from django.conf import settings
from django.core.exceptions import BadRequest as DjangoBadRequest
from django.core.exceptions import (
    PermissionDenied,
    RequestDataTooBig,
    SuspiciousOperation,
    TooManyFieldsSent,
    TooManyFilesSent,
)
from django.http import Http404, HttpRequest, HttpResponse
from django.http.multipartparser import MultiPartParserError
from django.utils.log import log_response

from hattori.responses import APIReturn

if TYPE_CHECKING:
    from hattori import HattoriAPI  # pragma: no cover
    from hattori.params.models import ParamModel  # pragma: no cover

__all__ = [
    "ConfigError",
    "AuthenticationError",
    "AuthorizationError",
    "ValidationError",
    "ValidationErrorBody",
    "ValidationErrorDetail",
    "ValidationErrorResponse",
    "set_validation_error_model",
    "get_validation_error_model",
    "HttpError",
    "HttpErrorBody",
    "HttpErrorResponse",
    "set_http_error_model",
    "get_http_error_model",
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


class HttpErrorBody(pydantic.BaseModel):
    """Base for the body of an :class:`HttpError` response.

    One model drives both sides of the contract: the default ``HttpError``
    handler builds the response by calling :meth:`from_error`, and OpenAPI
    documents its schema as the 400 every operation with a request body can
    return (a body that cannot be parsed raises ``HttpError(400)``). Subclass
    it and install the subclass with :func:`set_http_error_model` to change the
    shape without the spec and the wire drifting apart::

        class Problem(HttpErrorBody):
            code: str
            message: str

            @classmethod
            def from_error(cls, error):
                status = HTTPStatus(error.status_code)
                return cls(code=status.name.lower(), message=str(error))

        set_http_error_model(Problem)
    """

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        """Build the response body from the :class:`HttpError` that was raised."""
        raise NotImplementedError


# The default body: ``{"detail": "<message>"}``.
class HttpErrorResponse(HttpErrorBody):
    detail: str

    @classmethod
    def from_error(cls, error: HttpError) -> Self:
        return cls(detail=str(error))


_http_error_model: type[HttpErrorBody] = HttpErrorResponse


def set_http_error_model(model: type[HttpErrorBody]) -> None:
    """Set the project-wide :class:`HttpError` response body model.

    Drives the OpenAPI schema of an unparseable request body's 400 and the
    default ``HttpError`` handler together. Call once at startup, e.g. from an
    ``AppConfig.ready()`` hook.
    """
    if not (isinstance(model, type) and issubclass(model, HttpErrorBody)):
        raise ConfigError(
            f"{model!r} must subclass hattori.HttpErrorBody and implement from_error()."
        )
    global _http_error_model
    _http_error_model = model


def get_http_error_model() -> type[HttpErrorBody]:
    return _http_error_model


class ValidationErrorBody(pydantic.BaseModel):
    """Base for the body of a 422 response.

    One model drives both sides of the contract: the OpenAPI 422 schema is
    generated from it, and the default ``ValidationError`` handler builds the
    response by calling :meth:`from_errors`. Subclass it and install the
    subclass with :func:`set_validation_error_model` to change the shape
    without the spec and the wire drifting apart::

        class Problem(ValidationErrorBody):
            code: Literal["validation_error"] = "validation_error"
            fields: list[FieldProblem]

            @classmethod
            def from_errors(cls, errors):
                return cls(fields=[FieldProblem.of(e) for e in errors])

        set_validation_error_model(Problem)
    """

    @classmethod
    def from_errors(cls, errors: list[dict[str, Any]]) -> Self:
        """Build the response body from a :class:`ValidationError`'s errors.

        Each entry has ``loc`` (a tuple, request-relative — see
        ``HattoriAPI.validation_error_from_error_contexts``), ``msg``, ``type``,
        and for some error types a ``ctx``.
        """
        raise NotImplementedError


class ValidationErrorDetail(pydantic.BaseModel):
    # pydantic attaches a ``ctx`` to many error types, and it is part of what
    # the API actually sends. Carrying it through keeps the documented shape
    # honest instead of quietly narrower than the response.
    model_config = pydantic.ConfigDict(extra="allow")

    loc: list[str | int]
    msg: str
    type: str


# The default 422 body: ``{"detail": [{loc, msg, type}, ...]}``.
class ValidationErrorResponse(ValidationErrorBody):
    detail: list[ValidationErrorDetail]

    @classmethod
    def from_errors(cls, errors: list[dict[str, Any]]) -> Self:
        return cls.model_validate({"detail": errors})


_validation_error_model: type[ValidationErrorBody] = ValidationErrorResponse


def set_validation_error_model(model: type[ValidationErrorBody]) -> None:
    """Set the project-wide 422 response body model.

    Drives the OpenAPI 422 schema and the default ``ValidationError`` handler
    together. Call once at startup, e.g. from an ``AppConfig.ready()`` hook.
    """
    if not (isinstance(model, type) and issubclass(model, ValidationErrorBody)):
        raise ConfigError(
            f"{model!r} must subclass hattori.ValidationErrorBody and implement "
            "from_errors()."
        )
    global _validation_error_model
    _validation_error_model = model


def get_validation_error_model() -> type[ValidationErrorBody]:
    return _validation_error_model


class ErrorBody(pydantic.BaseModel):
    """Default error response body shipped with hattori: ``{code, message}``.

    Use with :class:`ApiError` for a zero-boilerplate error pattern. If your API
    uses a different shape, subclass :class:`~hattori.APIReturn` directly with
    your own body type instead.
    """

    code: str = pydantic.Field(
        description=(
            "Stable, machine-readable error code. Branch on this value rather than "
            "on `message`."
        )
    )
    message: str = pydantic.Field(
        description=(
            "Human-readable explanation of the error. The wording can change, so "
            "don't parse it."
        )
    )

    # The error class a narrowed body was generated for, so the OpenAPI schema
    # can show that class's declared message as the example.
    __hattori_error__: ClassVar[type[Any] | None] = None


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


def narrowed_error_body(error: type, error_code: str) -> type[ErrorBody]:
    """A body model for ``error`` whose ``code`` is the constant ``error_code``."""
    base = resolve_error_body_base(error)
    code_field = deepcopy(base.model_fields["code"])
    # Literal already restricts both the value and its type; Pydantic cannot
    # apply a str field's Strict metadata to a literal schema. A fixed code
    # must also bypass string normalization, which could change its value.
    code_field.metadata = [
        replace(item, strict=None, to_upper=None, to_lower=None, strip_whitespace=None)
        if isinstance(item, pydantic.StringConstraints)
        else item
        for item in code_field.metadata
        if not isinstance(item, pydantic.Strict)
    ]
    body: type[ErrorBody] = pydantic.create_model(
        error.__name__,
        __base__=base,
        __module__=error.__module__,
        # The base's own field rides along, so a description declared on it is
        # still there once the type is narrowed.
        code=(Annotated[Literal[error_code], code_field], ...),
    )
    # ApiError supplies code and message by their Python names. Accept names
    # on this generated model while retaining aliases and the base's validators.
    # populate_by_name covers Pydantic <2.11; validate_by_name overrides an
    # explicit alias-only configuration on newer versions.
    body.model_config.update({"populate_by_name": True, "validate_by_name": True})
    body.model_rebuild(force=True)
    body.__hattori_error__ = error
    return body


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
            cls.__hattori_response_body__ = narrowed_error_body(cls, error_code)

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
    for exc_class, answer in _DJANGO_EXCEPTIONS.items():
        api.add_exception_handler(
            exc_class,
            partial(_default_django_exception, api=api, answer=answer),
        )
    api.add_exception_handler(
        HttpError,
        partial(_default_http_error, api=api),
    )
    api.add_exception_handler(
        ValidationError,
        partial(_default_validation_error, api=api),
    )


# Django's own exceptions, and the HttpError each one is answered as: the arms of
# ``django.core.handlers.exception.response_for_exception`` that are not a 500.
# Raised inside the API they keep the status Django gives them, but take the same
# body, through the same handlers, as every other HttpError.
_DJANGO_EXCEPTIONS: dict[type[Exception], HttpError] = {
    Http404: HttpError(404, "Not Found"),
    PermissionDenied: AuthorizationError(),
    MultiPartParserError: HttpError(400, "Bad Request"),
    DjangoBadRequest: HttpError(400, "Bad Request"),
    SuspiciousOperation: HttpError(400, "Bad Request"),
}


def _default_django_exception(
    request: HttpRequest, exc: Exception, api: HattoriAPI, answer: HttpError
) -> HttpResponse:
    if isinstance(exc, (RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent)):
        # As Django does: reading the POST data again, which error reporting
        # does, would raise the same exception again.
        request._mark_post_parse_error()  # type: ignore[attr-defined]
    message = answer.message
    # The reason on a Django exception is written for the developer, so it only
    # reaches the client in DEBUG.
    if settings.DEBUG and str(exc):
        message = f"{message}: {exc}"
    error = type(answer)(answer.status_code, message)
    error.__cause__ = exc
    try:
        response = api.on_exception(request, error)
    except Exception:
        # The answer failed, which Django reports in its own right. What it
        # would have logged about the exception itself is still owed.
        _log_as_django_does(request, exc, HttpResponse(status=error.status_code))
        raise
    _log_as_django_does(request, exc, response)
    return response


def _log_as_django_does(
    request: HttpRequest, exc: Exception, response: HttpResponse
) -> None:
    # The log entry each arm of ``response_for_exception`` writes, so that
    # answering in Django's place loses nothing from the logs. Http404 has no
    # entry of its own there either.
    if isinstance(exc, PermissionDenied):
        log_response(
            "Forbidden (Permission denied): %s",
            request.path,
            response=response,
            request=request,
            exception=exc,
        )
    elif isinstance(exc, MultiPartParserError):
        log_response(
            "Bad request (Unable to parse request body): %s",
            request.path,
            response=response,
            request=request,
            exception=exc,
        )
    elif isinstance(exc, DjangoBadRequest):
        log_response(
            "%s: %s",
            str(exc),
            request.path,
            response=response,
            request=request,
            exception=exc,
        )
    elif isinstance(exc, SuspiciousOperation):
        log_response(
            str(exc),
            response=response,
            request=request,
            exception=exc,
            level="error",
            logger=logging.getLogger(f"django.security.{type(exc).__name__}"),
        )


def _default_http_error(
    request: HttpRequest, exc: HttpError, api: HattoriAPI
) -> HttpResponse:
    body = get_http_error_model().from_error(exc)
    return api.create_response(request, body, status=exc.status_code)


def _default_validation_error(
    request: HttpRequest, exc: ValidationError, api: HattoriAPI
) -> HttpResponse:
    body = get_validation_error_model().from_errors(exc.errors)
    return api.create_response(request, body, status=422)


def _default_exception(
    request: HttpRequest, exc: Exception, api: HattoriAPI
) -> HttpResponse:
    if not settings.DEBUG:
        raise exc  # let django deal with it

    logger.exception(exc)
    tb = traceback.format_exc()
    return HttpResponse(tb, status=500, content_type="text/plain")
