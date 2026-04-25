import collections.abc
import re
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    TypeVar,
)

from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern, URLResolver, reverse
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
from hattori.openapi.schema import OpenAPISchema
from hattori.openapi.urls import get_openapi_urls, get_root_url
from hattori.renderers import BaseRenderer, JSONRenderer
from hattori.router import BoundRouter, Router, RouterMount, _OperationOptions
from hattori.types import TCallable

if TYPE_CHECKING:
    from .operation import Operation  # pragma: no cover

__all__ = ["HattoriAPI"]

_E = TypeVar("_E", bound=Exception)
Exc = _E | type[_E]
ExcHandler = Callable[[HttpRequest, Exc[_E]], HttpResponse]


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
        docs: DocsBase = Swagger(),
        docs_url: str | None = "/docs",
        docs_decorator: Callable[[TCallable], TCallable] | None = None,
        servers: list[dict[str, Any]] | None = None,
        urls_namespace: str | None = None,
        auth: collections.abc.Sequence[Callable]
        | Callable
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
            openapi_url: The relative URL to serve the openAPI spec.
            openapi_extra: Additional attributes for the openAPI spec.
            docs_url: The relative URL to serve the API docs.
            servers: List of target hosts used in openAPI spec.
            auth (Callable | Sequence[Callable] | NOT_SET_TYPE | None): Authentication class
            renderer: Default response renderer
        """
        self.title = title
        self.version = version
        self.description = description
        self.openapi_url = openapi_url
        self.docs = docs
        self.docs_url = docs_url
        self.docs_decorator = docs_decorator
        self.servers = servers or []
        self.urls_namespace = urls_namespace or f"api-{self.version}"
        self.renderer = renderer or JSONRenderer()
        self._content_type = (
            f"{self.renderer.media_type}; charset={self.renderer.charset}"
        )
        self.openapi_extra = openapi_extra or {}

        self._exception_handlers: dict[Exc, ExcHandler] = {}
        self.set_default_exception_handlers()

        self.auth: collections.abc.Sequence[Callable] | NOT_SET_TYPE | None

        if callable(auth):
            self.auth = [auth]
        else:
            self.auth = auth

        # Top-level router registrations (new architecture)
        # Stores (prefix, router, auth, tags, url_name_prefix) for each add_router call
        self._router_registrations: list[
            tuple[str, Router, Any, list[str] | None, str | None]
        ] = []
        self._bound_routers_cache: list[BoundRouter] | None = None

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
            **options.with_default_auth(self.auth).as_kwargs(),
        )

    def get(
        self,
        path: str,
        *,
        auth: Any = NOT_SET,
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
            ),
        )

    def post(
        self,
        path: str,
        *,
        auth: Any = NOT_SET,
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
            ),
        )

    def delete(
        self,
        path: str,
        *,
        auth: Any = NOT_SET,
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
            ),
        )

    def patch(
        self,
        path: str,
        *,
        auth: Any = NOT_SET,
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
            ),
        )

    def put(
        self,
        path: str,
        *,
        auth: Any = NOT_SET,
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
            ),
        )

    def api_operation(
        self,
        methods: list[str],
        path: str,
        *,
        auth: Any = NOT_SET,
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
        tags: list[str] | None = None,
        url_name_prefix: str | None = None,
        parent_router: Router | None = None,
    ) -> None:
        """
        Add a router to this API.

        Args:
            prefix: URL prefix for all routes in the router
            router: Router instance or import path string
            auth: Authentication override for this router
            tags: Tags override for this router
            url_name_prefix: Prefix for URL names (required when mounting same router multiple times)
            parent_router: Internal use - parent router for nested routers
        """
        # Prevent adding routers after URLs have been generated
        if self._bound_routers_cache is not None:
            raise ConfigError(
                "Cannot add routers after URLs have been generated. "
                "Add all routers before accessing api.urls"
            )

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
                # Pass auth/tags so they can be inherited by children
                mounts = router.build_routers(
                    prefix,
                    api_decorators,
                    inherited_auth=auth,
                    inherited_tags=tags,
                )

                # Apply mount-level overrides to the first (parent) mount
                # build_routers() always returns at least one mount (the router itself)
                first_mount = mounts[0]
                if auth is not NOT_SET:
                    first_mount.auth = auth
                if tags is not None:
                    first_mount.tags = tags

                # Apply url_name_prefix to all mounts
                if url_name_prefix is not None:
                    for mount in mounts:
                        mount.url_name_prefix = url_name_prefix

                all_mounts.extend(mounts)

            # Create bound routers from mounts
            self._bound_routers_cache = [
                BoundRouter(mount, self) for mount in all_mounts
            ]

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

    def get_root_path(self, path_params: dict[str, Any]) -> str:
        name = f"{self.urls_namespace}:api-root"
        return reverse(name, kwargs=path_params)

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
        if path_prefix is None:
            path_prefix = self.get_root_path(path_params or {})
        return get_schema(api=self, path_prefix=path_prefix)

    def get_openapi_operation_id(
        self, operation: "Operation", router: BoundRouter
    ) -> str:
        name = operation.view_func.__name__
        prefix = re.sub(r"\{[^}]+\}", "", router.prefix or "")
        prefix = re.sub(r"/+", "/", prefix).strip("/")
        if prefix:
            return f"{prefix.replace('/', '_')}_{name}"
        return name

    def get_operation_url_name(self, operation: "Operation", router: Router) -> str:
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
        if handler is None:
            raise exc
        return handler(request, exc)

    def validation_error_from_error_contexts(
        self, error_contexts: list[ValidationErrorContext]
    ) -> ValidationError:
        errors: list[dict[str, Any]] = []
        for context in error_contexts:
            model = context.model
            e = context.pydantic_validation_error
            for i in e.errors(include_url=False):
                i["loc"] = (
                    model.__hattori_param_source__,
                ) + model.__hattori_flatten_map_reverse__.get(i["loc"], i["loc"])
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

    def _lookup_exception_handler(self, exc: Exc[_E]) -> ExcHandler[_E] | None:
        for cls in type(exc).__mro__:
            if cls in self._exception_handlers:
                return self._exception_handlers[cls]

        return None
