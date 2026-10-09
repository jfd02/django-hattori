import collections.abc
import inspect
from collections import Counter
from collections.abc import Callable, Iterator
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    TypeAliasType,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

import pydantic
from asgiref.sync import async_to_sync, sync_to_async
from django.db import connections, transaction
from django.http import (
    HttpRequest,
    HttpResponse,
    StreamingHttpResponse,
)
from django.http.response import HttpResponseBase
from pydantic import BaseModel

from hattori.compatibility.files import FIX_MIDDLEWARE_PATH, need_to_fix_request_files
from hattori.compatibility.util import UNION_TYPES
from hattori.constants import NOT_SET, NOT_SET_TYPE
from hattori.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigError,
    HttpError,
    ValidationErrorContext,
)
from hattori.params.models import TModels
from hattori.responses import APIReturn, dump_model
from hattori.returns import (
    DeclaredResponse,
    DeclaredResponses,
    alias_value,
    declared_response,
    is_alias,
    union_arms,
    without_metadata,
)
from hattori.schema import Schema
from hattori.security.base import auth_can_decline, declared_auth_responses
from hattori.security.permissions import validate_permissions
from hattori.signature import ViewSignature
from hattori.streaming import StreamFormat, _serialize_item
from hattori.utils import await_result, close_unawaited, is_async_callable

if TYPE_CHECKING:
    from hattori import HattoriAPI  # pragma: no cover

__all__ = ["Guard", "Operation", "PathView"]

# Sentinel marking that a streamed generator produced no items at all.
_NO_FIRST_ITEM = object()


def rollback_atomic_requests(request: HttpRequest) -> None:
    """Fail the request's ATOMIC_REQUESTS transaction, as an error response does."""
    # A database the route opted out of with non_atomic_requests has no
    # request transaction, and one opened further out is not ours to end.
    match = request.resolver_match
    non_atomic = getattr(match.func, "_non_atomic_requests", ()) if match else ()
    for db in connections.all():
        if (
            db.settings_dict.get("ATOMIC_REQUESTS")
            and db.alias not in non_atomic
            and db.in_atomic_block
        ):
            transaction.set_rollback(True, using=db.alias)


def drop_stream_for_head(
    request: HttpRequest, response: HttpResponseBase
) -> HttpResponseBase:
    """Keep a HEAD request from pulling a streamed body.

    The server drops a HEAD body, but only after reading a stream or a file to
    its end. The original is still closed with the response.
    """
    if request.method == "HEAD" and isinstance(response, StreamingHttpResponse):
        response.streaming_content = _no_chunks() if response.is_async else ()
    return response


async def _no_chunks() -> collections.abc.AsyncIterator[bytes]:
    empty: tuple[bytes, ...] = ()
    for chunk in empty:
        yield chunk


def _reject_unrun_result(result: Any, owner: str) -> None:
    """Refuse a ``result`` that stands for code which has not run.

    An awaitable, a generator and an async generator are all truthy before
    their body has decided anything, so taking one for a principal or for a
    passed check would let the request through unchecked. Whether to await is
    read off the result, not off whether the callback looks async - a sync
    decorator around an ``async def`` hands back a coroutine all the same - and
    it is awaited once: what is still unrun after that is refused here.
    """
    if not (
        inspect.isawaitable(result)
        or inspect.isgenerator(result)
        or inspect.isasyncgen(result)
    ):
        return
    close_unawaited(result)
    raise ConfigError(
        f"{owner} returned {type(result).__name__} where a result was expected: "
        f"its code has not run, so nothing was checked."
    )


class _ParsedAnnotation:
    __slots__ = ("responses", "stream_format", "stream_status")

    def __init__(self) -> None:
        self.responses = DeclaredResponses()
        self.stream_format: type[StreamFormat] | None = None
        self.stream_status: int | None = None


def _find_model_dump_override(schema: Any) -> type | None:
    """The first model in a pydantic core schema that overrides ``model_dump``."""
    if isinstance(schema, dict):
        cls = schema.get("cls")
        if (
            schema.get("type") == "model"
            and isinstance(cls, type)
            and getattr(cls, "model_dump", None) is not BaseModel.model_dump
        ):
            return cls
        schema = list(schema.values())
    if isinstance(schema, (list, tuple)):
        for item in schema:
            found = _find_model_dump_override(item)
            if found is not None:
                return found
    return None


def _stream_format(tp: Any) -> type[StreamFormat] | None:
    """The format ``tp`` streams in, if it is a stream such as ``JSONL[Item]``."""
    origin = get_origin(tp)
    if isinstance(origin, type) and issubclass(origin, StreamFormat):
        return origin
    return None


def _is_response(arm: Any) -> bool:
    """Whether ``arm`` is a response of its own: an ``APIReturn`` or a stream."""
    arm = without_metadata(arm)
    cls = get_origin(arm) or arm
    return isinstance(cls, type) and issubclass(cls, (APIReturn, StreamFormat))


def _response_arms(annotation: Any) -> Iterator[Any]:
    """The arms of an endpoint's return annotation.

    A ``type`` alias, or ``Annotated``, is opened up where a response of its
    own stands behind it, an ``APIReturn`` or a stream, so the union may be
    written behind one. With none behind it, it names a body: it stays whole,
    to be documented as it is written, but for a generic alias, which is
    filled in.
    """
    if get_origin(annotation) in UNION_TYPES:
        for arm in get_args(annotation):
            yield from _response_arms(arm)
        return
    wraps = is_alias(annotation) or get_origin(annotation) is Annotated
    if wraps and any(_is_response(arm) for arm in union_arms(annotation)):
        behind = (
            alias_value(annotation)
            if is_alias(annotation)
            else without_metadata(annotation)
        )
        yield from _response_arms(behind)
    elif isinstance(get_origin(annotation), TypeAliasType):
        yield alias_value(annotation)
    else:
        yield annotation


def _parse_return_annotation(view_func: Callable) -> _ParsedAnnotation:
    """Read the responses declared by the function's return type annotation.

    Supports two arm forms (mixable in a Union):
        -> UserOut                    # bare type = implicit status 200
        -> UserOut | UserNotFound     # APIReturn subclass = its .code
    """
    hints = get_type_hints(view_func, include_extras=True)
    annotation = hints.get("return", inspect.Parameter.empty)

    # If the function has no return annotation, check __wrapped__ (for decorators
    # that don't use functools.wraps)
    if annotation is inspect.Parameter.empty and hasattr(view_func, "__wrapped__"):
        hints = get_type_hints(view_func.__wrapped__, include_extras=True)
        annotation = hints.get("return", inspect.Parameter.empty)

    if annotation is inspect.Parameter.empty:
        raise ConfigError(
            f"Function {view_func.__name__} must have a return type annotation."
        )

    parsed = _ParsedAnnotation()
    arms_per_status: Counter[int] = Counter()
    bare_bodies = 0
    for arm in _response_arms(annotation):
        declared = declared_response(arm, view_func.__name__)
        is_bare = declared is None
        if declared is None:
            declared = DeclaredResponse(code=200, body=arm, description="")

        # Bare, or as the body of a response: ``Created[JSONL[Item]]``.
        body = declared.body
        stream = without_metadata(body)
        stream_format = _stream_format(stream)
        if stream_format is not None:
            if parsed.stream_format is not None:
                raise ConfigError(
                    f"{view_func.__name__} declares more than one stream in its "
                    f"return type. An operation streams one thing."
                )
            parsed.stream_format = stream_format
            parsed.stream_status = declared.code
            body = get_args(stream)[0]
        elif is_bare:
            bare_bodies += 1

        # Arms that share a status (e.g. two different 409 error types) are
        # combined into a union.
        parsed.responses.add(declared.code, body, declared.description)
        arms_per_status[declared.code] += 1

    if parsed.stream_status is not None and arms_per_status[parsed.stream_status] > 1:
        # Which of the two a result is could only be guessed at.
        raise ConfigError(
            f"{view_func.__name__} declares both a stream and another response "
            f"for status {parsed.stream_status}. Give the other one a status of "
            f"its own."
        )
    if parsed.stream_format is not None and bare_bodies:
        # What the view returns bare is its stream, so the other could not be
        # told from it either.
        raise ConfigError(
            f"{view_func.__name__} declares a stream and a bare response beside "
            f"it. What it returns bare is its stream: declare the other as a "
            f"response class."
        )

    return parsed


def _merge_response_schemas(
    collected: dict[Any, Any], responses: dict[int, Any]
) -> None:
    """Fold ``{code: schema}`` entries from auth/permissions into ``collected``.

    A code already present with a different schema becomes a union of the two so
    every declared variant is documented; ``None``-bodied entries are overwritten.
    """
    for code, schema_type in responses.items():
        existing = collected.get(code)
        if existing is None or existing is type(None):
            collected[code] = schema_type
        elif existing is schema_type:
            continue
        else:
            collected[code] = existing | schema_type


class Guard:
    """The auth and permissions in front of a view, and the answers they give.

    An :class:`Operation` is a guard with an endpoint behind it. A view that is
    not an operation, such as the docs, is put behind one on its own.
    """

    def __init__(
        self,
        api: HattoriAPI | None = None,
        *,
        auth: collections.abc.Sequence[Callable]
        | Callable
        | NOT_SET_TYPE
        | None = NOT_SET,
        permissions: collections.abc.Sequence[Any]
        | Any
        | NOT_SET_TYPE
        | None = NOT_SET,
        csrf_exempt: bool = False,
        responses: DeclaredResponses | None = None,
        by_alias: bool = False,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
    ) -> None:
        # An operation is given its API when it is bound to one.
        self.api: HattoriAPI = cast("HattoriAPI", api)
        self.csrf_exempt: bool = csrf_exempt

        self.auth_param: (
            collections.abc.Sequence[Callable] | Callable | object | None
        ) = auth
        self.auth_callbacks: collections.abc.Sequence[Callable] = []
        self._set_auth(auth)

        self.permissions_param: collections.abc.Sequence[Any] | Any | None = permissions
        self.permission_callbacks: collections.abc.Sequence[Any] = []
        self._set_permissions(permissions)

        # Exporting models params
        self.by_alias = by_alias
        self.exclude_unset = exclude_unset
        self.exclude_defaults = exclude_defaults
        self.exclude_none = exclude_none

        # The responses declared by what is guarded itself, kept separate so
        # response_models can be rebuilt whenever auth/permissions are attached
        # after __init__ (e.g. inherited from a router or the API at bind time).
        declared = responses or DeclaredResponses()
        self._annotated_responses: dict[Any, Any] = declared.schemas
        self._annotated_descriptions: dict[int, list[str]] = declared.descriptions
        self.response_models: dict[Any, Any]
        self._build_response_models()

    def _label(self) -> str:
        """What this is called in an error message."""
        return "The guard"

    def _build_response_models(self) -> None:
        """(Re)build ``response_models`` from the responses declared by what is
        guarded plus any auth/permission-declared ones currently attached.

        Auth and permissions may be attached *after* ``__init__`` — inherited
        from a router or the API when the operation is bound — so this folds
        their typed ``APIReturn`` responses in again. Keeping this idempotent and
        callable at bind time keeps both the OpenAPI spec and the runtime
        short-circuit dispatch (:meth:`_result_to_response`) in sync with the
        effective auth, no matter how it was supplied.
        """
        # Their APIReturn subclasses become valid response types both at
        # runtime (short-circuit) and in the OpenAPI spec. What each response
        # class says of itself goes to the spec: the endpoint's own first, then
        # its auth's and its permissions'.
        collected = dict(self._annotated_responses)
        described = [self._annotated_descriptions]
        for auth_cb in self.auth_callbacks:
            declared = declared_auth_responses(auth_cb)
            _merge_response_schemas(collected, declared.schemas)
            described.append(declared.descriptions)
        for permission in self.permission_callbacks:
            _merge_response_schemas(collected, permission.permission_responses)
            described.append(permission.permission_descriptions)

        self.response_models = {}
        for status_code, schema_type in collected.items():
            if schema_type is type(None):
                self.response_models[status_code] = None
            else:
                self.response_models[status_code] = self._create_response_model(
                    schema_type
                )

        # Descriptions that share a status follow one another, without repeats.
        by_code: dict[int, list[str]] = {}
        for descriptions in described:
            for code, found in descriptions.items():
                known = by_code.setdefault(code, [])
                known.extend(text for text in found if text not in known)
        self.response_descriptions: dict[int, str] = {
            code: "\n\n".join(found) for code, found in by_code.items()
        }

    def _set_auth(
        self, auth: collections.abc.Sequence[Callable] | Callable | object | None
    ) -> None:
        if auth is not None and auth is not NOT_SET:
            self.auth_callbacks = (
                auth
                if isinstance(auth, collections.abc.Sequence)
                else [cast("Callable[..., Any]", auth)]
            )
        self._index_auth_callbacks()

    def _index_auth_callbacks(self) -> None:
        """Precompute each auth callback's async-ness once, off the request path.

        ``is_async_callable`` walks ``inspect`` internals and the async-ness of a
        callback never changes, so pairing each callback with its flag here keeps
        that work off the per-request authentication loop. Called from every place
        that assigns ``auth_callbacks`` (``__init__`` and bind-time inheritance via
        ``_set_auth``) so the cache can never go stale.

        Which of them say they never decline is read here too: the spec and the
        check on what they return then go by the same answer.
        """
        self.auth_callbacks_with_async: list[tuple[Callable, bool]] = [
            (cb, is_async_callable(cb) or getattr(cb, "is_async", False))
            for cb in self.auth_callbacks
        ]
        self.auth_never_declining: list[Callable] = [
            cb for cb in self.auth_callbacks if not auth_can_decline(cb)
        ]

    def _set_permissions(
        self, permissions: collections.abc.Sequence[Any] | Any | None
    ) -> None:
        validate_permissions(permissions)
        if permissions is not None and permissions is not NOT_SET:
            self.permission_callbacks = (
                permissions
                if isinstance(permissions, collections.abc.Sequence)
                else [permissions]
            )

    def refuse(
        self, request: HttpRequest, path_params: dict[str, Any]
    ) -> HttpResponseBase | None:
        """Run the checks in front of a view that is not an operation.

        The response the request is refused with, or ``None`` if it may go on.
        """
        try:
            return self._run_checks(
                request, self.api.create_temporal_response(request), path_params
            )
        except Exception as exc:
            # A check whose result was rejected.
            return self._escaped_exception(request, exc)

    def _run_checks(
        self,
        request: HttpRequest,
        temporal_response: HttpResponse,
        path_params: dict[str, Any],
    ) -> HttpResponseBase | None:
        "Runs security checks for each operation"
        # NOTE: if you change anything in this function - do this also in AsyncOperation

        # Set CSRF exempt status on request so auth handlers can check it
        if self.csrf_exempt:
            # _hattori_csrf_exempt is a special flag that tells auth handler to skip CSRF checks
            request._hattori_csrf_exempt = True  # type: ignore

        # auth:
        if self.auth_callbacks:
            error = self._run_authentication(request, temporal_response)
            if error is not None:
                return error

        # permissions (run after auth so request.auth is available):
        if self.permission_callbacks:
            error = self._run_permissions(request, temporal_response, path_params)
            if error is not None:
                return error

        return None

    def _auth_outcome(
        self,
        request: HttpRequest,
        result: Any,
        temporal_response: HttpResponse,
        callback: Callable[..., Any],
    ) -> tuple[HttpResponseBase | None, bool]:
        """Map an auth callback ``result`` to ``(response, handled)``.

        ``handled`` True means stop looping: either a typed ``APIReturn`` or a
        response, which short-circuits to that response, or a successful auth
        whose value is stashed on ``request.auth``. ``handled`` False means this
        callback declined (returned a falsy value) - try the next one.
        Truthiness, not ``is not None``, so that ``return key == SECRET``
        rejects a wrong key. A callback that says it never declines is held to
        it: a falsy result from it is a misconfiguration.
        """
        name = getattr(callback, "__name__", type(callback).__name__)
        _reject_unrun_result(result, f"Auth {name}")
        if isinstance(result, (APIReturn, HttpResponseBase)):
            # Auth answered for itself - a typed error response, or a response
            # outright, which is no principal - so short-circuit to it instead
            # of calling the view.
            return self._result_to_response(request, result, temporal_response), True
        if result:
            request.auth = result  # type: ignore
            return None, True
        if any(callback is never for never in self.auth_never_declining):
            # Its promise is what keeps the default 401 out of the spec. The
            # result is named by its type only: it may hold a credential.
            raise ConfigError(
                f"Auth {name} has can_decline=False but returned a falsy "
                f"{type(result).__name__}. Return a truthy principal or a "
                f"response, or set can_decline=True."
            )
        return None, False

    def _run_authentication(
        self, request: HttpRequest, temporal_response: HttpResponse
    ) -> HttpResponseBase | None:
        for callback, _ in self.auth_callbacks_with_async:
            try:
                result = callback(request)
                if inspect.isawaitable(result):
                    result = async_to_sync(await_result)(result)
            except Exception as exc:
                return self._on_exception(request, exc)

            outcome, handled = self._auth_outcome(
                request, result, temporal_response, callback
            )
            if handled:
                return outcome
        return self._on_exception(request, AuthenticationError())

    def _permission_outcome(
        self,
        request: HttpRequest,
        result: Any,
        temporal_response: HttpResponse,
        permission: Any,
    ) -> HttpResponseBase | None:
        """Map a permission ``check`` result to a short-circuit response (or None).

        ``True`` means pass; ``False`` or ``None`` is a ``403`` using the
        permission's ``message``; an ``APIReturn`` or a response short-circuits
        to that response. Anything else is no verdict - an uncalled method and
        a ``(False, "reason")`` tuple are both truthy - so it is refused as a
        misconfiguration rather than read as a pass.
        """
        owner = f"{type(permission).__name__}.check"
        _reject_unrun_result(result, owner)
        if isinstance(result, (APIReturn, HttpResponseBase)):
            return self._result_to_response(request, result, temporal_response)
        if result is True:
            return None
        if result is False or result is None:
            return self._on_exception(
                request, AuthorizationError(message=permission.message)
            )
        raise ConfigError(
            f"{owner} returned {type(result).__name__}, which neither allows nor "
            f"refuses the request: return True, False or None, or a response."
        )

    def _run_permissions(
        self,
        request: HttpRequest,
        temporal_response: HttpResponse,
        path_params: dict[str, Any],
    ) -> HttpResponseBase | None:
        for permission in self.permission_callbacks:
            try:
                kwargs = permission.select_path_kwargs(path_params)
                result = permission.check(request, **kwargs)
                if inspect.isawaitable(result):
                    result = async_to_sync(await_result)(result)
            except Exception as exc:
                return self._on_exception(request, exc)

            outcome = self._permission_outcome(
                request, result, temporal_response, permission
            )
            if outcome is not None:
                return outcome
        return None

    def _escaped_exception(self, request: HttpRequest, exc: Exception) -> HttpResponse:
        """Answer an exception that got out of the checks or of ``run``.

        Either a rejected check result or a view decorator raised it, or the
        handlers already left it unanswered and it goes on to Django.
        """
        if self.api.left_unanswered(request, exc):
            raise exc
        return self._on_exception(request, exc)

    def _on_exception(self, request: HttpRequest, exc: Exception) -> HttpResponse:
        response = self.api.on_exception(request, exc)
        rollback_atomic_requests(request)
        return response

    def _result_to_response(
        self, request: HttpRequest, result: Any, temporal_response: HttpResponse
    ) -> HttpResponseBase:
        """
        The protocol for results:
         - if HttpResponse - returns as is
         - if APIReturn instance - code from type(result).code, body from result.value
         - otherwise - bare value, dispatched as the declared 200 schema
        """
        if isinstance(result, HttpResponseBase):
            return result

        status: int
        if isinstance(result, APIReturn):
            status = type(result).code
            if status >= 400:
                # A returned error fails the request just as a raised one does.
                rollback_atomic_requests(request)
            result = result.value
        else:
            # Bare return value - dispatch as the declared success code (200).
            if 200 not in self.response_models:
                raise ConfigError(
                    f"{self._label()} returned a bare value but no "
                    f"200 response is declared in its return annotation. "
                    f"Got: {type(result).__name__}"
                )
            status = 200

        if status in self.response_models:
            response_model = self.response_models[status]
        else:
            # Fall back to range matching: e.g., status 201 matches model for 200
            base_status = (status // 100) * 100
            if base_status in self.response_models:
                response_model = self.response_models[base_status]
            elif Ellipsis in self.response_models:
                response_model = self.response_models[Ellipsis]
            else:
                raise ConfigError(
                    f"Schema for status {status} is not set in response"
                    f" {self.response_models.keys()}"
                )

        temporal_response.status_code = status

        if response_model is None:
            # Nothing is rendered. Unless the view wrote a body of its own to
            # the response it was handed, there is none, and so no media type
            # to name for it either.
            if not temporal_response.content:
                del temporal_response["Content-Type"]
            return temporal_response

        ctx = {"request": request, "response_status": status}

        # Whatever the view returned is validated against the declared type and
        # dumped through it, so only the fields that type declares go out. An
        # instance of the declared model, or of a subclass, passes validation
        # as is; it is never dumped by its own class.
        validated_object = response_model.model_validate(
            {"response": result}, context=ctx
        )

        result = self._dump_model(validated_object, ctx)["response"]
        return self.api.create_response(
            request, result, temporal_response=temporal_response
        )

    def _dump_model(
        self, model: BaseModel, ctx: dict[str, Any], *, stream: bool = False
    ) -> dict[str, Any]:
        mode = (
            "json"
            if stream
            else getattr(self.api.renderer, "serialization_mode", "python")
        )
        dumped: dict[str, Any] = dump_model(
            model,
            mode,
            context=ctx,
            by_alias=self.by_alias,
            exclude_unset=self.exclude_unset,
            exclude_defaults=self.exclude_defaults,
            exclude_none=self.exclude_none,
        )
        return dumped

    def _create_response_model(self, response_param: Any) -> type[Schema] | None:
        if response_param is None:
            return None
        attrs = {"__annotations__": {"response": response_param}}
        model: type[Schema] = type("HattoriResponseSchema", (Schema,), attrs)
        # Responses are dumped by the declared type's pydantic serializer, which
        # never calls model_dump. Refuse an override rather than ignore it.
        overriding = _find_model_dump_override(model.__pydantic_core_schema__)
        if overriding is not None:
            raise ConfigError(
                f"{self._label()} responds with "
                f"{overriding.__name__}, which overrides model_dump. Responses "
                f"are serialized without calling model_dump, so the override "
                f"would be ignored. Use @model_serializer or Field(exclude=True) "
                f"to shape the output instead."
            )
        return model


class Operation(Guard):
    def __init__(
        self,
        path: str,
        methods: list[str],
        view_func: Callable,
        *,
        auth: collections.abc.Sequence[Callable]
        | Callable
        | NOT_SET_TYPE
        | None = NOT_SET,
        permissions: collections.abc.Sequence[Any]
        | Any
        | NOT_SET_TYPE
        | None = NOT_SET,
        operation_id: str | None = None,
        summary: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        deprecated: bool | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool | None = None,
        exclude_defaults: bool | None = None,
        exclude_none: bool | None = None,
        include_in_schema: bool = True,
        url_name: str | None = None,
        openapi_extra: dict[str, Any] | None = None,
    ) -> None:
        self.is_async = False
        self.path: str = path
        self.methods: list[str] = methods
        self.view_func: Callable = view_func
        if url_name is not None:
            self.url_name = url_name

        # Refused before the view is read: reading its return type settles the
        # bodies of the errors it names.
        validate_permissions(permissions)

        self.signature = ViewSignature(self.path, self.view_func)
        self.models: TModels = self.signature.models

        # Parse response schema from return type annotation
        parsed = _parse_return_annotation(view_func)
        self.stream_format: type[StreamFormat] | None = parsed.stream_format
        self.stream_item_model: type[Schema] | None = None
        self._stream_status: int | None = parsed.stream_status

        super().__init__(
            auth=auth,
            permissions=permissions,
            csrf_exempt=getattr(view_func, "csrf_exempt", False),
            responses=parsed.responses,
            by_alias=by_alias or False,
            exclude_unset=exclude_unset or False,
            exclude_defaults=exclude_defaults or False,
            exclude_none=exclude_none or False,
        )

        if need_to_fix_request_files(methods, self.models):
            raise ConfigError(
                f"Router '{path}' has method(s) {methods}  that require fixing request.FILES. "
                f"Please add '{FIX_MIDDLEWARE_PATH}' to settings.MIDDLEWARE"
            )

        self.operation_id = operation_id
        self.summary = summary or self.view_func.__name__.title().replace("_", " ")
        self.description = description or self.signature.docstring
        self.tags = tags
        self.deprecated = deprecated
        self.include_in_schema = include_in_schema
        self.openapi_extra = openapi_extra

        if hasattr(view_func, "_hattori_contribute_to_operation"):
            # Allow 3rd party code to contribute to the operation behavior
            callbacks: list[Callable] = view_func._hattori_contribute_to_operation
            for callback in callbacks:
                callback(self)

    def _label(self) -> str:
        return f"View {self.view_func.__name__}"

    def _build_response_models(self) -> None:
        super()._build_response_models()
        if self.stream_format:
            # The stream's own response, wherever in the annotation it stands.
            self.stream_item_model = self.response_models[self._stream_status]

    def clone(self) -> Operation:
        """
        Create a fresh copy of this operation for binding to an API.

        This method is used when mounting the same router multiple times
        to ensure each mount has independent operation instances.
        """
        # Create instance without calling __init__ to avoid expensive processing
        cloned = object.__new__(self.__class__)

        # Copy all essential attributes
        cloned.is_async = self.is_async
        cloned.path = self.path
        cloned.methods = list(self.methods)
        cloned.view_func = self.view_func
        cloned.api = cast("HattoriAPI", None)  # Will be set during binding
        cloned.csrf_exempt = self.csrf_exempt

        # Copy url_name if it exists
        if hasattr(self, "url_name"):
            cloned.url_name = self.url_name

        # Copy auth settings
        cloned.auth_param = self.auth_param
        cloned.auth_callbacks = list(self.auth_callbacks)
        cloned.auth_callbacks_with_async = list(self.auth_callbacks_with_async)
        cloned.auth_never_declining = list(self.auth_never_declining)

        # Copy permission settings
        cloned.permissions_param = self.permissions_param
        cloned.permission_callbacks = list(self.permission_callbacks)

        # Copy signature and models (immutable after creation, safe to share)
        cloned.signature = self.signature
        cloned.models = self.models

        # Copy streaming attributes
        cloned.stream_format = self.stream_format
        cloned.stream_item_model = self.stream_item_model
        cloned._stream_status = self._stream_status

        # Copy response models (dict copy for isolation)
        cloned.response_models = dict(self.response_models)
        # Return-annotation responses, so the clone can rebuild response_models
        # if auth/permissions are attached during binding (read-only, safe to share).
        cloned._annotated_responses = self._annotated_responses
        cloned._annotated_descriptions = self._annotated_descriptions
        cloned.response_descriptions = dict(self.response_descriptions)

        # Copy metadata
        cloned.operation_id = self.operation_id
        cloned.summary = self.summary
        cloned.description = self.description
        cloned.tags = list(self.tags) if self.tags else None
        cloned.deprecated = self.deprecated
        cloned.include_in_schema = self.include_in_schema
        cloned.openapi_extra = dict(self.openapi_extra) if self.openapi_extra else None

        # Copy export model params
        cloned.by_alias = self.by_alias
        cloned.exclude_unset = self.exclude_unset
        cloned.exclude_defaults = self.exclude_defaults
        cloned.exclude_none = self.exclude_none

        # Re-apply run decorators (from decorate_view) to the clone's run method
        # We can't just copy the decorated run because it's bound to the original instance
        if hasattr(self, "_run_decorators") and self._run_decorators:
            cloned._run_decorators = []  # type: ignore[attr-defined]
            for deco in self._run_decorators:
                cloned.run = deco(cloned.run)  # type: ignore
                cloned._run_decorators.append(deco)  # type: ignore[attr-defined]

        return cloned

    def run(self, request: HttpRequest, **kw: Any) -> HttpResponseBase:
        temporal_response = self.api.create_temporal_response(request)
        error = self._run_checks(request, temporal_response, kw)
        if error is not None:
            return error
        try:
            values = self._get_values(request, kw, temporal_response)
            if self.stream_format:
                self._set_stream_status(temporal_response)
            result = self.view_func(request, **values)
            if self.stream_format:
                return self._stream_response(request, result, temporal_response)
            return self._result_to_response(request, result, temporal_response)
        except Exception as e:
            self._add_wraps_hint(e)
            return self._on_exception(request, e)

    def _add_wraps_hint(self, exc: Exception) -> None:
        if isinstance(exc, TypeError) and "required positional argument" in str(exc):
            msg = "Did you fail to use functools.wraps() in a decorator?"
            msg = f"{exc.args[0]}: {msg}" if exc.args else msg
            exc.args = (msg,) + exc.args[1:]

    def _copy_temporal_response(
        self, temporal_response: HttpResponse, response: StreamingHttpResponse
    ) -> None:
        for key, value in temporal_response.items():
            if key.lower() != "content-type":
                response[key] = value
        for cookie_name, cookie in temporal_response.cookies.items():
            response.cookies[cookie_name] = cookie

    def _set_stream_status(self, temporal_response: HttpResponse) -> None:
        """Start the response on the status the stream is declared under.

        ``Created[JSONL[Item]]`` streams as a 201. Set before the view runs, so
        that one which sets a status of its own still has the last word.
        """
        assert self._stream_status is not None
        temporal_response.status_code = self._stream_status

    def _unwrap_stream(self, result: Any) -> Any:
        """``result`` without the response its stream is declared in.

        A stream declared as ``Created[JSONL[Item]]`` may be returned as
        ``Created(items())``. No other response shares the stream's status, so
        one returned under it is the stream.
        """
        if isinstance(result, APIReturn) and type(result).code == self._stream_status:
            return result.value
        return result

    def _create_streaming_response(
        self, content: Any, temporal_response: HttpResponse
    ) -> StreamingHttpResponse:
        assert self.stream_format is not None
        response = StreamingHttpResponse(
            content,
            content_type=self.stream_format.media_type,
            status=temporal_response.status_code,
        )
        for key, value in self.stream_format.response_headers().items():
            response[key] = value
        return response

    def _validate_stream_item(
        self, item: Any, request: HttpRequest, ctx: dict[str, Any]
    ) -> str:
        """Validate a single stream item and return serialized JSON string."""
        assert self.stream_item_model is not None
        validated = self.stream_item_model.model_validate(
            {"response": item}, context=ctx
        )

        result = self._dump_model(validated, ctx, stream=True)["response"]
        return _serialize_item(result)

    def _stream_response(
        self,
        request: HttpRequest,
        generator: Any,
        temporal_response: HttpResponse,
    ) -> HttpResponseBase:
        """Create a StreamingHttpResponse from a sync generator.

        Unless the view answered with one of the other responses it declares
        beside the stream, which is then sent as any operation's would be.
        """
        assert self.stream_format is not None
        fmt = self.stream_format
        generator = self._unwrap_stream(generator)
        if isinstance(generator, (APIReturn, HttpResponseBase)):
            return self._result_to_response(request, generator, temporal_response)

        # Prime the generator up to its first yield (running the view body up to
        # that point) so any headers/cookies/status it sets before streaming are
        # captured now. WSGI flushes the status line and headers *before* iterating
        # the body, so they must be copied onto the response before it is returned —
        # copying them when the generator finishes is too late to reach the client.
        # Headers set mid-stream cannot be honored; this is documented behavior.
        try:
            first_item = next(generator)
        except StopIteration as stop:
            if isinstance(stop.value, (APIReturn, HttpResponseBase)):
                # A generator that returned one before its first yield. After
                # that the headers are out, and a returned value has no way out.
                return self._result_to_response(request, stop.value, temporal_response)
            first_item = _NO_FIRST_ITEM

        # Built after priming so a status set before the first yield is reflected.
        ctx = {"request": request, "response_status": temporal_response.status_code}

        def content_iter() -> Any:
            if first_item is _NO_FIRST_ITEM:
                return
            yield fmt.format_chunk(self._validate_stream_item(first_item, request, ctx))
            for item in generator:
                yield fmt.format_chunk(self._validate_stream_item(item, request, ctx))

        response = self._create_streaming_response(content_iter(), temporal_response)
        self._copy_temporal_response(temporal_response, response)
        return response

    def _get_values(
        self, request: HttpRequest, path_params: Any, temporal_response: HttpResponse
    ) -> dict[str, Any]:
        values = {}
        error_contexts: list[ValidationErrorContext] = []
        for model in self.models:
            try:
                data = model.resolve(request, self.api, path_params)
                values.update(data)
            except pydantic.ValidationError as e:
                error_contexts.append(
                    ValidationErrorContext(pydantic_validation_error=e, model=model)
                )
        if error_contexts:
            validation_error = self.api.validation_error_from_error_contexts(
                error_contexts
            )
            raise validation_error
        if self.signature.response_arg:
            values[self.signature.response_arg] = temporal_response
        return values


class AsyncOperation(Operation):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.is_async = True

    async def run(self, request: HttpRequest, **kw: Any) -> HttpResponseBase:  # type: ignore
        temporal_response = self.api.create_temporal_response(request)
        error = await self._run_checks(request, temporal_response, kw)
        if error is not None:
            return error
        try:
            values = self._get_values(request, kw, temporal_response)
            if self.stream_format:
                self._set_stream_status(temporal_response)
                result = self.view_func(request, **values)
                if inspect.isawaitable(result):
                    # No async generator but a coroutine: it returns the
                    # stream, or one of the other responses declared beside it.
                    result = await result
                return await self._async_stream_response(
                    request, result, temporal_response
                )
            result = await self.view_func(request, **values)
            return self._result_to_response(request, result, temporal_response)
        except Exception as e:
            self._add_wraps_hint(e)
            return await self._aon_exception(request, e)

    async def _aon_exception(
        self, request: HttpRequest, exc: Exception
    ) -> HttpResponse:
        # Handlers are sync code that may use the ORM: never on the event loop.
        return await sync_to_async(self._on_exception)(request, exc)

    async def _async_stream_response(
        self,
        request: HttpRequest,
        generator: Any,
        temporal_response: HttpResponse,
    ) -> HttpResponseBase:
        """Create a StreamingHttpResponse from an async generator.

        Unless the view answered with one of the other responses it declares
        beside the stream. An async generator cannot return a value, so it is
        only a view that returns its stream which can.
        """
        assert self.stream_format is not None
        fmt = self.stream_format
        generator = self._unwrap_stream(generator)
        if isinstance(generator, (APIReturn, HttpResponseBase)):
            return self._result_to_response(request, generator, temporal_response)

        # Prime the generator up to its first yield so headers/cookies/status the
        # view sets before streaming are captured now. ASGI flushes the response
        # start (status + headers) before iterating the body, so they must be copied
        # onto the response before it is returned. Headers set mid-stream cannot be
        # honored; this is documented behavior.
        try:
            first_item = await anext(generator)
        except StopAsyncIteration:
            first_item = _NO_FIRST_ITEM

        # Built after priming so a status set before the first yield is reflected.
        ctx = {"request": request, "response_status": temporal_response.status_code}

        async def content_iter() -> Any:
            if first_item is _NO_FIRST_ITEM:
                return
            yield fmt.format_chunk(self._validate_stream_item(first_item, request, ctx))
            async for item in generator:
                yield fmt.format_chunk(self._validate_stream_item(item, request, ctx))

        response = self._create_streaming_response(content_iter(), temporal_response)
        self._copy_temporal_response(temporal_response, response)
        return response

    async def _run_checks(  # type: ignore
        self,
        request: HttpRequest,
        temporal_response: HttpResponse,
        path_params: dict[str, Any],
    ) -> HttpResponseBase | None:
        "Runs security checks for each operation"
        # NOTE: if you change anything in this function - do this also in Sync Operation

        # Set CSRF exempt status on request so auth handlers can check it
        if self.csrf_exempt:
            request._hattori_csrf_exempt = True  # type: ignore

        # auth:
        if self.auth_callbacks:
            error = await self._run_authentication(request, temporal_response)
            if error is not None:
                return error

        # permissions (run after auth so request.auth is available):
        if self.permission_callbacks:
            error = await self._run_permissions(request, temporal_response, path_params)
            if error is not None:
                return error

        return None

    async def _run_authentication(  # type: ignore
        self, request: HttpRequest, temporal_response: HttpResponse
    ) -> HttpResponseBase | None:
        for callback, is_async in self.auth_callbacks_with_async:
            try:
                if is_async:
                    result = callback(request)
                else:
                    result = await sync_to_async(callback)(request)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                return await self._aon_exception(request, exc)

            outcome, handled = self._auth_outcome(
                request, result, temporal_response, callback
            )
            if handled:
                return outcome
        return await self._aon_exception(request, AuthenticationError())

    async def _run_permissions(  # type: ignore
        self,
        request: HttpRequest,
        temporal_response: HttpResponse,
        path_params: dict[str, Any],
    ) -> HttpResponseBase | None:
        for permission in self.permission_callbacks:
            try:
                kwargs = permission.select_path_kwargs(path_params)
                if permission.is_async:
                    result = permission.check(request, **kwargs)
                else:
                    result = await sync_to_async(permission.check)(request, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                return await self._aon_exception(request, exc)

            # A refusal is answered by a handler too.
            outcome = await sync_to_async(self._permission_outcome)(
                request, result, temporal_response, permission
            )
            if outcome is not None:
                return outcome
        return None


class PathView:
    def __init__(self) -> None:
        self.operations: list[Operation] = []
        self._method_map: dict[str, Operation] = {}
        self.is_async = False  # if at least one operation is async - will become True
        self.url_name: str | None = None

    def add_operation(
        self,
        path: str,
        methods: list[str],
        view_func: Callable,
        *,
        auth: collections.abc.Sequence[Callable]
        | Callable
        | NOT_SET_TYPE
        | None = NOT_SET,
        permissions: collections.abc.Sequence[Any]
        | Any
        | NOT_SET_TYPE
        | None = NOT_SET,
        operation_id: str | None = None,
        summary: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        deprecated: bool | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool | None = None,
        exclude_defaults: bool | None = None,
        exclude_none: bool | None = None,
        url_name: str | None = None,
        include_in_schema: bool = True,
        openapi_extra: dict[str, Any] | None = None,
    ) -> Operation:
        duplicate_methods = set(methods) & self._method_map.keys()
        if duplicate_methods:
            raise ConfigError(
                f"Duplicate method(s) {sorted(duplicate_methods)} for path '{path}'"
            )

        if url_name:
            self.url_name = url_name

        OperationClass = Operation
        if is_async_callable(view_func) or inspect.isasyncgenfunction(view_func):
            self.is_async = True
            OperationClass = AsyncOperation

        operation = OperationClass(
            path,
            methods,
            view_func,
            auth=auth,
            permissions=permissions,
            operation_id=operation_id,
            summary=summary,
            description=description,
            tags=tags,
            deprecated=deprecated,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            include_in_schema=include_in_schema,
            url_name=url_name,
            openapi_extra=openapi_extra,
        )

        self.operations.append(operation)
        for method in methods:
            self._method_map[method] = operation
        view_func._hattori_operation = operation  # type: ignore

        return operation

    @property
    def api(self) -> HattoriAPI:
        return self.operations[0].api

    def clone(self) -> PathView:
        """
        Create a fresh copy of this PathView with cloned operations.

        This method is used when mounting the same router multiple times
        to ensure each mount has independent PathView and Operation instances.
        """
        cloned = PathView()
        cloned.is_async = self.is_async
        cloned.url_name = self.url_name
        cloned.operations = [op.clone() for op in self.operations]
        cloned._method_map = {
            method: op for op in cloned.operations for method in op.methods
        }
        return cloned

    def get_view(self) -> Callable:
        # Create a unique view function for this PathView

        if self.is_async:
            # Create a wrapper for async view
            async def async_view_wrapper(
                request: HttpRequest, *args: Any, **kwargs: Any
            ) -> HttpResponseBase:
                return await self._async_view(request, *args, **kwargs)

            # All django-hattori views are CSRF exempt at Django middleware level
            # Cookie-based auth (APIKeyCookie) handles CSRF checking separately
            async_view_wrapper.csrf_exempt = True  # type: ignore

            return self._mark_non_atomic(async_view_wrapper)
        else:
            # Create a wrapper for sync view
            def sync_view_wrapper(
                request: HttpRequest, *args: Any, **kwargs: Any
            ) -> HttpResponseBase:
                return self._sync_view(request, *args, **kwargs)

            # All django-hattori views are CSRF exempt at Django middleware level
            # Cookie-based auth (APIKeyCookie) handles CSRF checking separately
            sync_view_wrapper.csrf_exempt = True  # type: ignore

            return self._mark_non_atomic(sync_view_wrapper)

    def _mark_non_atomic(self, view: Callable) -> Callable:
        """Carry ``transaction.non_atomic_requests`` over to the URL callback.

        Django reads the marker off the callback it resolved, which is the
        wrapper built above rather than the decorated endpoint. That one
        callback serves every method of the path, so a database is opted out
        only when all of them opted out.
        """
        non_atomic: set[str] | None = None
        for operation in self.operations:
            marker = set(getattr(operation.view_func, "_non_atomic_requests", ()))
            non_atomic = marker if non_atomic is None else non_atomic & marker
        if non_atomic:
            view._non_atomic_requests = non_atomic  # type: ignore
        return view

    def _sync_view(self, request: HttpRequest, *a: Any, **kw: Any) -> HttpResponseBase:
        response: HttpResponseBase
        operation = self._find_operation(request)
        if operation is None:
            response = self._undeclared_method(request)
        else:
            response = self._run(operation, request, *a, **kw)
        return drop_stream_for_head(request, response)

    async def _async_view(
        self, request: HttpRequest, *a: Any, **kw: Any
    ) -> HttpResponseBase:
        # Whatever is answered here may run a handler, so not on the event loop.
        response: HttpResponseBase
        operation = self._find_operation(request)
        if operation is None:
            response = await sync_to_async(self._undeclared_method)(request)
        elif not operation.is_async:
            response = await sync_to_async(self._run)(operation, request, *a, **kw)
        else:
            try:
                response = await cast(AsyncOperation, operation).run(request, *a, **kw)
            except Exception as exc:
                response = await sync_to_async(operation._escaped_exception)(
                    request, exc
                )
        return drop_stream_for_head(request, response)

    def _run(
        self, operation: Operation, request: HttpRequest, /, *a: Any, **kw: Any
    ) -> HttpResponseBase:
        try:
            return operation.run(request, *a, **kw)
        except Exception as exc:
            return operation._escaped_exception(request, exc)

    def _find_operation(self, request: HttpRequest) -> Operation | None:
        method = request.method or ""
        operation = self._method_map.get(method)
        if operation is None and method == "HEAD":
            operation = self._implicit_head()
        return operation

    def _implicit_head(self) -> Operation | None:
        """The GET operation, where it also answers HEAD, as in Django's View.

        Not when HEAD is declared, nor for a declared stream, whose response is
        only built by starting it.
        """
        if "HEAD" in self._method_map:
            return None
        operation = self._method_map.get("GET")
        if operation is None or operation.stream_format:
            return None
        return operation

    def _allowed_methods(self) -> list[str]:
        methods = list(self._method_map)
        if self._implicit_head():
            methods.append("HEAD")
        if "OPTIONS" not in self._method_map:
            methods.append("OPTIONS")
        return methods

    def _undeclared_method(self, request: HttpRequest) -> HttpResponse:
        """Answer a method that no operation on this path declares."""
        if request.method == "OPTIONS":
            # As in Django's View: no body, only Allow.
            response = self.api.create_temporal_response(request)
            response["Content-Length"] = "0"
        else:
            response = self.api.on_exception(
                request, HttpError(405, "Method Not Allowed")
            )
            rollback_atomic_requests(request)
        response["Allow"] = ", ".join(self._allowed_methods())
        return response
