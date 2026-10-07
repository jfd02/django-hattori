import collections.abc
import re
import threading
from collections.abc import Callable
from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
)

from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.urls import URLPattern, URLResolver, get_resolver, get_urlconf, reverse
from django.utils.module_loading import import_string

from hattori.constants import NOT_SET, NOT_SET_TYPE
from hattori.decorators import DecoratorMode
from hattori.errors import (
    ConfigError,
    ValidationError,
    ValidationErrorContext,
    set_default_exc_handlers,
)
from hattori.openapi import get_schema
from hattori.openapi.docs import DocsBase, Swagger
from hattori.openapi.schema import OpenAPISchema, get_operation_id
from hattori.openapi.urls import (
    get_openapi_urls,
    get_root_url,
    shared_namespace_error,
)
from hattori.renderers import BaseRenderer, JSONRenderer
from hattori.router import BoundRouter, Router, RouterMount, _OperationOptions
from hattori.security.permissions import validate_permissions
from hattori.types import TCallable

if TYPE_CHECKING:
    from .operation import Operation  # pragma: no cover

__all__ = ["HattoriAPI"]

_E = TypeVar("_E", bound=Exception)
type Exc[E: Exception] = E | type[E]
type ExcHandler[E: Exception] = Callable[[HttpRequest, Exc[E]], HttpResponse]


class HattoriAPI:
    """
    Hattori API
    """

    def __init__(
        self,
        *,
        title: str = "HattoriAPI",
        version: str = "1.0.0",
        description: str = "",
        openapi_url: str | None = "/openapi.json",
        docs: DocsBase | None = None,
        docs_url: str | None = "/docs",
        docs_decorator: Callable[[TCallable], TCallable] | None = None,
        docs_auth: collections.abc.Sequence[Callable]
        | Callable
        | NOT_SET_TYPE
        | None = NOT_SET,
        servers: list[dict[str, Any]] | None = None,
        urls_namespace: str | None = None,
        auth: collections.abc.Sequence[Callable]
        | Callable
        | NOT_SET_TYPE
        | None = NOT_SET,
        permissions: collections.abc.Sequence[Any]
        | Any
        | NOT_SET_TYPE
        | None = NOT_SET,
        renderer: BaseRenderer | None = None,
        default_router: Router | None = None,
        openapi_extra: dict[str, Any] | None = None,
    ):
        """
        Args:
            title: A title for the api.
            description: A description for the api.
            version: The API version.
            urls_namespace: The Django URL namespace for the API. If not provided, the namespace will be ``"api-" + self.version``.
                APIs mounted in the same URLconf each need their own.
            openapi_url: The relative URL to serve the openAPI spec.
            openapi_extra: Additional attributes for the openAPI spec.
            docs_url: The relative URL to serve the API docs.
            docs_decorator: A decorator applied to the docs and openAPI spec views.
            docs_auth: Authentication for the docs and the openAPI spec. Left
                unset they are guarded like the API's own operations, by ``auth``
                and ``permissions``; ``None`` makes them public.
            servers: List of target hosts used in openAPI spec.
            auth (Callable | Sequence[Callable] | NOT_SET_TYPE | None): Authentication class
            renderer: Default response renderer
        """
        self.title = title
        self.version = version
        self.description = description
        self.openapi_url = openapi_url
        self.docs = docs or Swagger()
        self.docs_url = docs_url
        self.docs_decorator = docs_decorator
        self.servers = servers or []
        self.urls_namespace = urls_namespace or f"api-{self.version}"
        self.renderer = renderer or JSONRenderer()
        self._content_type = (
            f"{self.renderer.media_type}; charset={self.renderer.charset}"
        )
        self.openapi_extra = openapi_extra or {}

        self._exception_handlers: dict[type[Exception], ExcHandler[Any]] = {}
        self.set_default_exception_handlers()

        self.auth: collections.abc.Sequence[Callable] | NOT_SET_TYPE | None

        if callable(auth):
            self.auth = [auth]
        else:
            self.auth = auth

        self.docs_auth: collections.abc.Sequence[Callable] | NOT_SET_TYPE | None
        self.docs_auth = [docs_auth] if callable(docs_auth) else docs_auth

        validate_permissions(permissions)
        # Permissions: a single BasePermission isn't callable, so normalize by
        # wrapping any non-sequence (and non-sentinel) value into a list.
        self.permissions: collections.abc.Sequence[Any] | NOT_SET_TYPE | None
        if (
            permissions is NOT_SET
            or permissions is None
            or isinstance(permissions, collections.abc.Sequence)
        ):
            self.permissions = permissions
        else:
            self.permissions = [permissions]

        # Top-level router registrations (new architecture)
        # Stores (prefix, router, auth, tags, url_name_prefix) for each add_router call
        self._router_registrations: list[
            tuple[str, Router, Any, Any, list[str] | None, str | None]
        ] = []
        self._bound_routers_cache: list[BoundRouter] | None = None
        self._openapi_schema: OpenAPISchema | None = None
        self._openapi_schema_lock = threading.Lock()

        # Backward compat: keep _routers list populated
        self._routers: list[tuple[str, Router]] = []

        self.default_router = default_router or Router()
        self.add_router("", self.default_router)

    def _default_api_operation(
        self, methods: list[str], path: str, options: _OperationOptions
    ) -> Callable[[TCallable], TCallable]:
        return self.default_router.api_operation(
            methods,
            path,
            **options
            .with_default_auth(self.auth)
            .with_default_permissions(self.permissions)
            .as_kwargs(),
        )

    def get(
        self,
        path: str,
        *,
        auth: Any = NOT_SET,
        permissions: Any = NOT_SET,
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
    ) -> Callable[[TCallable], TCallable]:
        """
        `GET` operation. See <a href="../operations-parameters">operations
        parameters</a> reference.
        """
        return self._default_api_operation(
            ["GET"],
            path,
            _OperationOptions(
                auth,
                operation_id,
                summary,
                description,
                tags,
                deprecated,
                by_alias,
                exclude_unset,
                exclude_defaults,
                exclude_none,
                url_name,
                include_in_schema,
                openapi_extra,
                permissions=permissions,
            ),
        )

    def post(
        self,
        path: str,
        *,
        auth: Any = NOT_SET,
        permissions: Any = NOT_SET,
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
    ) -> Callable[[TCallable], TCallable]:
        """
        `POST` operation. See <a href="../operations-parameters">operations
        parameters</a> reference.
        """
        return self._default_api_operation(
            ["POST"],
            path,
            _OperationOptions(
                auth,
                operation_id,
                summary,
                description,
                tags,
                deprecated,
                by_alias,
                exclude_unset,
                exclude_defaults,
                exclude_none,
                url_name,
                include_in_schema,
                openapi_extra,
                permissions=permissions,
            ),
        )

    def delete(
        self,
        path: str,
        *,
        auth: Any = NOT_SET,
        permissions: Any = NOT_SET,
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
    ) -> Callable[[TCallable], TCallable]:
        """
        `DELETE` operation. See <a href="../operations-parameters">operations
        parameters</a> reference.
        """
        return self._default_api_operation(
            ["DELETE"],
            path,
            _OperationOptions(
                auth,
                operation_id,
                summary,
                description,
                tags,
                deprecated,
                by_alias,
                exclude_unset,
                exclude_defaults,
                exclude_none,
                url_name,
                include_in_schema,
                openapi_extra,
                permissions=permissions,
            ),
        )

    def patch(
        self,
        path: str,
        *,
        auth: Any = NOT_SET,
        permissions: Any = NOT_SET,
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
    ) -> Callable[[TCallable], TCallable]:
        """
        `PATCH` operation. See <a href="../operations-parameters">operations
        parameters</a> reference.
        """
        return self._default_api_operation(
            ["PATCH"],
            path,
            _OperationOptions(
                auth,
                operation_id,
                summary,
                description,
                tags,
                deprecated,
                by_alias,
                exclude_unset,
                exclude_defaults,
                exclude_none,
                url_name,
                include_in_schema,
                openapi_extra,
                permissions=permissions,
            ),
        )

    def put(
        self,
        path: str,
        *,
        auth: Any = NOT_SET,
        permissions: Any = NOT_SET,
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
    ) -> Callable[[TCallable], TCallable]:
        """
        `PUT` operation. See <a href="../operations-parameters">operations
        parameters</a> reference.
        """
        return self._default_api_operation(
            ["PUT"],
            path,
            _OperationOptions(
                auth,
                operation_id,
                summary,
                description,
                tags,
                deprecated,
                by_alias,
                exclude_unset,
                exclude_defaults,
                exclude_none,
                url_name,
                include_in_schema,
                openapi_extra,
                permissions=permissions,
            ),
        )

    def api_operation(
        self,
        methods: list[str],
        path: str,
        *,
        auth: Any = NOT_SET,
        permissions: Any = NOT_SET,
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
    ) -> Callable[[TCallable], TCallable]:
        return self._default_api_operation(
            methods,
            path,
            _OperationOptions(
                auth,
                operation_id,
                summary,
                description,
                tags,
                deprecated,
                by_alias,
                exclude_unset,
                exclude_defaults,
                exclude_none,
                url_name,
                include_in_schema,
                openapi_extra,
                permissions=permissions,
            ),
        )

    def add_decorator(
        self,
        decorator: Callable,
        mode: DecoratorMode = "operation",
    ) -> None:
        """
        Add a decorator to be applied to all operations in the entire API.

        Args:
            decorator: The decorator function to apply
            mode: "operation" (default) applies after validation,
                  "view" applies before validation
        """
        # Store decorator on default router - will be inherited by all routers during build
        self.default_router.add_decorator(decorator, mode)

    def add_router(
        self,
        prefix: str,
        router: Router | str,
        *,
        auth: Any = NOT_SET,
        permissions: Any = NOT_SET,
        tags: list[str] | None = None,
        url_name_prefix: str | None = None,
    ) -> None:
        """
        Add a router to this API.

        Args:
            prefix: URL prefix for all routes in the router
            router: Router instance or import path string
            auth: Authentication override for this router
            permissions: Permissions override for this router
            tags: Tags override for this router
            url_name_prefix: Prefix for URL names (required when mounting same router multiple times)
        """
        # Prevent adding routers after URLs have been generated
        if self._bound_routers_cache is not None:
            raise ConfigError(
                "Cannot add routers after URLs have been generated. "
                "Add all routers before accessing api.urls"
            )
        validate_permissions(permissions)

        if isinstance(router, str):
            router = import_string(router)
            assert isinstance(router, Router)

        # Check for duplicate router template - require url_name_prefix
        existing_templates = {reg[1] for reg in self._router_registrations}
        if router in existing_templates and url_name_prefix is None:
            raise ConfigError(
                "Router is already mounted to this API. When mounting the same router "
                "multiple times, you must provide unique url_name_prefix for each mount."
            )

        # Store registration for later processing during URL generation
        # This allows child routers to be added after add_router() is called
        self._router_registrations.append((
            prefix,
            router,
            auth,
            permissions,
            tags,
            url_name_prefix,
        ))

        # Backward compat: keep _routers list updated (just the top-level router)
        self._routers.append((prefix, router))

    @property
    def urls(self) -> tuple[list[URLResolver | URLPattern], str, str]:
        """
        str: URL configuration

        Returns:

            Django URL configuration
        """
        return (
            self._get_urls(),
            "hattori",
            self.urls_namespace.split(":")[-1],
            # ^ if api included into nested urls, we only care about last bit here
        )

    def _get_bound_routers(self) -> list[BoundRouter]:
        """Get or create bound router instances."""
        if self._bound_routers_cache is None:
            # Build mounts from registrations (delayed to capture all child routers)
            all_mounts: list[RouterMount] = []

            for (
                prefix,
                router,
                auth,
                permissions,
                tags,
                url_name_prefix,
            ) in self._router_registrations:
                # Get API-level decorators from default router
                api_decorators = (
                    self.default_router._decorators
                    if router is not self.default_router
                    else []
                )

                # Build mount configurations (non-mutating)
                # auth/permissions are this mount's overrides; they apply to the
                # router and are inherited by its children. Tags are passed so
                # they can be inherited by children.
                mounts = router.build_routers(
                    prefix,
                    api_decorators,
                    inherited_tags=tags,
                    mount_auth=auth,
                    mount_permissions=permissions,
                )

                # Apply the mount-level tags override to the first (parent) mount
                # build_routers() always returns at least one mount (the router itself)
                first_mount = mounts[0]
                if tags is not None:
                    first_mount.tags = tags

                # Apply url_name_prefix to all mounts
                if url_name_prefix is not None:
                    for mount in mounts:
                        mount.url_name_prefix = url_name_prefix

                all_mounts.extend(mounts)

            # Create bound routers from mounts
            bound_routers = [BoundRouter(mount, self) for mount in all_mounts]
            # Checked before the routers are kept, so a startup that failed
            # here fails again if the URLconf is imported a second time.
            self._validate_unique_operation_ids(bound_routers)
            self._bound_routers_cache = bound_routers

            # Freeze all templates after binding
            for mount in all_mounts:
                mount.template._freeze()

            # Update _routers for backward compat (include all nested routers)
            self._routers = [(m.prefix, m.template) for m in all_mounts]

        return self._bound_routers_cache

    def _get_urls(self) -> list[URLResolver | URLPattern]:
        result = get_openapi_urls(self)

        for bound_router in self._get_bound_routers():
            result.extend(bound_router.urls_paths(bound_router.prefix))

        result.append(get_root_url(self))
        self._validate_unique_url_names(result)
        return result

    def _validate_unique_url_names(
        self, patterns: list[URLResolver | URLPattern]
    ) -> None:
        seen_names: set[str] = set()
        for pattern in patterns:
            if not isinstance(pattern, URLPattern):
                continue
            if not pattern.name:
                continue
            if pattern.name in seen_names:
                raise ConfigError(
                    f"Duplicate URL name '{pattern.name}' detected in API "
                    f"namespace '{self.urls_namespace}'. Use unique url_name or "
                    f"url_name_prefix values."
                )
            seen_names.add(pattern.name)

    def _validate_unique_operation_ids(self, bound_routers: list[BoundRouter]) -> None:
        seen: dict[str, Callable] = {}
        for bound_router in bound_routers:
            for path_view in bound_router.path_operations.values():
                for operation in path_view.operations:
                    if not operation.include_in_schema:
                        continue
                    for method in operation.methods:
                        op_id = get_operation_id(self, operation, bound_router, method)
                        if op_id in seen:
                            first, view = seen[op_id], operation.view_func
                            raise ConfigError(
                                f'Duplicate operation_id "{op_id}" '
                                f"(at {first.__module__}.{first.__name__} "
                                f"and {view.__module__}.{view.__name__}). "
                                "Pass an explicit operation_id= or rename the view."
                            )
                        seen[op_id] = operation.view_func

    def get_root_path(self, path_params: dict[str, Any]) -> str:
        return self._own_url("api-root", path_params)

    def _own_url(self, name: str, path_params: dict[str, Any]) -> str:
        """The url of one of the API's own views, found by its namespace."""
        error = shared_namespace_error(self, get_resolver(get_urlconf()))
        if error:
            raise ConfigError(error)
        return reverse(f"{self.urls_namespace}:{name}", kwargs=path_params)

    def create_response(
        self,
        request: HttpRequest,
        data: Any,
        *,
        status: int | None = None,
        temporal_response: HttpResponse | None = None,
    ) -> HttpResponse:
        if temporal_response:
            status = temporal_response.status_code
        assert status is not None

        content = self.renderer.render(request, data, response_status=status)

        if temporal_response:
            response = temporal_response
            response.content = content
        else:
            response = HttpResponse(
                content, status=status, content_type=self.get_content_type()
            )

        return response

    def create_temporal_response(self, request: HttpRequest) -> HttpResponse:
        return HttpResponse("", content_type=self.get_content_type())

    def get_content_type(self) -> str:
        return self._content_type

    def get_openapi_schema(
        self,
        *,
        path_prefix: str | None = None,
        path_params: dict[str, Any] | None = None,
    ) -> OpenAPISchema:
        """The OpenAPI document for this API.

        It is built once, on first use, and shared by every later call:
        treat the result as read-only.
        """
        if path_prefix is None:
            path_prefix = self.get_root_path(path_params or {})
        schema = self._openapi_schema
        if schema is None or not schema.is_current():
            # Building takes long enough that requests arriving together
            # should wait for one build rather than each run their own.
            with self._openapi_schema_lock:
                schema = self._openapi_schema
                if schema is None or not schema.is_current():
                    schema = get_schema(api=self, path_prefix=path_prefix)
                    self._openapi_schema = schema
        return schema.with_path_prefix(path_prefix)

    def get_openapi_operation_id(
        self, operation: Operation, router: BoundRouter
    ) -> str:
        name = operation.view_func.__name__
        prefix = re.sub(r"\{[^}]+\}", "", router.prefix or "")
        prefix = re.sub(r"/+", "/", prefix).strip("/")
        if prefix:
            return f"{prefix.replace('/', '_')}_{name}"
        return name

    def get_operation_url_name(self, operation: Operation, router: Router) -> str:
        """
        Get the default URL name to use for an operation if it wasn't
        explicitly provided.
        """
        return operation.view_func.__name__

    def add_exception_handler(
        self, exc_class: type[_E], handler: ExcHandler[_E]
    ) -> None:
        assert issubclass(exc_class, Exception)
        self._exception_handlers[exc_class] = handler

    def exception_handler(
        self, exc_class: type[Exception]
    ) -> Callable[[TCallable], TCallable]:
        def decorator(func: TCallable) -> TCallable:
            self.add_exception_handler(exc_class, func)
            return func

        return decorator

    def set_default_exception_handlers(self) -> None:
        set_default_exc_handlers(self)

    def on_exception(self, request: HttpRequest, exc: Exc[_E]) -> HttpResponse:
        handler = self._lookup_exception_handler(exc)
        try:
            if handler is None:
                raise exc
            response = handler(request, exc)
            if not isinstance(response, HttpResponseBase):
                # Not an answer, and auth and permissions read "no response"
                # as "allowed": a handler that forgets its return must not
                # turn their refusal into a pass.
                raise ConfigError(
                    f"The exception handler for {type(exc).__name__} returned "
                    f"{type(response).__name__}, not a response."
                ) from exc
            return response
        except Exception as unanswered:
            # Noted so that PathView, which may catch this again on its way to
            # Django, does not offer it to the handlers twice.
            request._hattori_unanswered = unanswered  # type: ignore[attr-defined]
            raise

    def validation_error_from_error_contexts(
        self, error_contexts: list[ValidationErrorContext]
    ) -> ValidationError:
        errors: list[dict[str, Any]] = []
        for context in error_contexts:
            model = context.model
            e = context.pydantic_validation_error
            for i in e.errors(include_url=False):
                loc = self._public_error_loc(model, i["loc"])
                i["loc"] = (model.__hattori_param_source__,) + loc
                # removing pydantic hints
                i.pop("input", None)  # type: ignore
                if (
                    "ctx" in i
                    and "error" in i["ctx"]
                    and isinstance(i["ctx"]["error"], Exception)
                ):
                    i["ctx"]["error"] = str(i["ctx"]["error"])
                errors.append(dict(i))
        return ValidationError(errors)

    @staticmethod
    def _public_error_loc(model: Any, loc: tuple[Any, ...]) -> tuple[Any, ...]:
        """Translate a pydantic ``loc`` into the client-facing path.

        Names that only exist inside the param model are removed, so ``loc``
        describes the request as the client sent it. A single body param is
        wrapped under the handler's argument name (see
        ``BodyModel.get_request_data``) even though the payload sits at the top
        level of the body — that argument is an implementation detail of the
        handler, and renaming it must not change the wire contract.
        """
        wrapper = getattr(model, "__read_from_single_attr__", None)
        if wrapper and loc[:1] == (wrapper,):
            loc = loc[1:]
        flattened: tuple[Any, ...] = model.__hattori_flatten_map_reverse__.get(loc, loc)
        return flattened

    def _lookup_exception_handler(self, exc: Exc[_E]) -> ExcHandler[_E] | None:
        for cls in type(exc).__mro__:
            if cls in self._exception_handlers:
                return self._exception_handlers[cls]

        return None
