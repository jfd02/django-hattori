import collections.abc
import re
from dataclasses import dataclass, field, replace
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
)

from django.urls import URLPattern
from django.urls import path as django_path
from django.utils.module_loading import import_string

from hattori.constants import NOT_SET
from hattori.decorators import DecoratorMode
from hattori.errors import ConfigError
from hattori.operation import PathView
from hattori.types import TCallable
from hattori.utils import normalize_path, replace_path_param_notation

if TYPE_CHECKING:
    from hattori import HattoriAPI  # pragma: no cover


__all__ = ["Router", "RouterMount", "BoundRouter"]


@dataclass
class RouterMount:
    """
    Configuration for how a Router template is mounted to an API.

    This class stores the mount-time configuration without mutating the
    original Router template, enabling router reuse across multiple APIs
    or multiple mount points within the same API.
    """

    template: "Router"
    prefix: str
    url_name_prefix: str | None = None
    auth: Any = NOT_SET
    tags: list[str] | None = None
    inherited_decorators: list[tuple[Callable, DecoratorMode]] = field(
        default_factory=list
    )
    # Inherited auth/tags from parent routers (for nested router inheritance)
    inherited_auth: Any = NOT_SET
    inherited_tags: list[str] | None = None


@dataclass(frozen=True)
class _OperationOptions:
    auth: Any = NOT_SET
    operation_id: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    deprecated: bool | None = None
    by_alias: bool | None = None
    exclude_unset: bool | None = None
    exclude_defaults: bool | None = None
    exclude_none: bool | None = None
    url_name: str | None = None
    include_in_schema: bool = True
    openapi_extra: dict[str, Any] | None = None

    def with_default_auth(self, auth: Any) -> "_OperationOptions":
        if self.auth is not NOT_SET:
            return self
        return replace(self, auth=auth)

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "auth": self.auth,
            "operation_id": self.operation_id,
            "summary": self.summary,
            "description": self.description,
            "tags": self.tags,
            "deprecated": self.deprecated,
            "by_alias": self.by_alias,
            "exclude_unset": self.exclude_unset,
            "exclude_defaults": self.exclude_defaults,
            "exclude_none": self.exclude_none,
            "url_name": self.url_name,
            "include_in_schema": self.include_in_schema,
            "openapi_extra": self.openapi_extra,
        }


class BoundRouter:
    """
    A Router template bound to a specific API instance.

    Contains cloned operations with decorators applied. Each mount of a router
    creates a new BoundRouter instance, ensuring complete isolation between mounts.
    """

    def __init__(self, mount: RouterMount, api: "HattoriAPI") -> None:
        self.mount = mount
        self.template = mount.template
        self.api = api
        self.prefix = mount.prefix
        self.url_name_prefix = mount.url_name_prefix

        # Effective settings priority:
        # 1. mount override (from api.add_router auth/tags params on this specific mount)
        # 2. template's own settings (set on the Router itself)
        # 3. inherited from parent (for nested routers where parent has auth)
        if mount.auth is not NOT_SET:
            self.auth = mount.auth
        elif mount.template.auth is not NOT_SET:
            self.auth = mount.template.auth
        elif mount.inherited_auth is not NOT_SET:
            self.auth = mount.inherited_auth
        else:
            self.auth = NOT_SET

        # Tags handling (issue #794):
        # - mount.tags (from add_router call) = explicit override, use as-is
        # - Otherwise, accumulate: inherited tags + template's own tags
        self.tags: list[str] | None
        if mount.tags is not None:
            # Explicit tags from add_router() call - use as override
            self.tags = mount.tags
        else:
            # Accumulate inherited tags with template's own tags
            accumulated_tags: list[str] = []
            if mount.inherited_tags is not None:
                accumulated_tags.extend(mount.inherited_tags)
            if mount.template.tags is not None:
                accumulated_tags.extend(mount.template.tags)
            self.tags = accumulated_tags or None

        # Clone operations and apply decorators
        self.path_operations: dict[str, PathView] = {}
        self._bind_operations()

    def _bind_operations(self) -> None:
        """Clone operations from template and apply effective settings."""
        effective_decorators = (
            self.mount.inherited_decorators + self.template._decorators
        )

        for path, path_view in self.template.path_operations.items():
            cloned_view = path_view.clone()

            for operation in cloned_view.operations:
                # Bind to API
                operation.api = self.api

                # Apply auth inheritance
                if operation.auth_param == NOT_SET:
                    if self.auth != NOT_SET:
                        operation._set_auth(self.auth)
                    elif self.api.auth != NOT_SET:
                        operation._set_auth(self.api.auth)

                # Apply tags inheritance
                if operation.tags is None and self.tags is not None:  # type: ignore[has-type]
                    operation.tags = self.tags  # type: ignore[has-type]

                # Apply decorators (fresh application - no tracking needed)
                for decorator, mode in effective_decorators:
                    if mode == "view":
                        operation.run = decorator(operation.run)  # type: ignore
                    elif mode == "operation":
                        operation.view_func = decorator(operation.view_func)
                    else:
                        raise ValueError(
                            f"Invalid decorator mode: {mode}"
                        )  # pragma: no cover

            self.path_operations[path] = cloned_view

    def urls_paths(self, prefix: str) -> collections.abc.Iterator[URLPattern]:
        """Generate URL patterns for this bound router."""
        prefix = replace_path_param_notation(prefix)
        for path, path_view in self.path_operations.items():
            path = replace_path_param_notation(path)
            route = "/".join([i for i in (prefix, path) if i])
            route = normalize_path(route)
            route = route.lstrip("/")

            for operation in path_view.operations:
                url_name = getattr(operation, "url_name", "")
                if not url_name:
                    url_name = self.api.get_operation_url_name(
                        operation, router=self.template
                    )
                    # Apply url_name_prefix if specified
                    if self.url_name_prefix and url_name:
                        url_name = f"{self.url_name_prefix}_{url_name}"

                yield django_path(route, path_view.get_view(), name=url_name)


class Router:
    def __init__(
        self,
        *,
        auth: Any = NOT_SET,
        tags: list[str] | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool | None = None,
        exclude_defaults: bool | None = None,
        exclude_none: bool | None = None,
    ) -> None:
        self._frozen = False
        self.auth = auth
        self.tags = tags
        self.by_alias = by_alias
        self.exclude_unset = exclude_unset
        self.exclude_defaults = exclude_defaults
        self.exclude_none = exclude_none

        self.path_operations: dict[str, PathView] = {}
        self._routers: list[
            tuple[str, Router, Any, list[str] | None, str | None]
        ] = []
        self._decorators: list[tuple[Callable, DecoratorMode]] = []

    def _freeze(self) -> None:
        """Mark router as frozen - no more modifications allowed."""
        self._frozen = True
        for _, child_router, _, _, _ in self._routers:
            child_router._freeze()

    def _check_not_frozen(self) -> None:
        """Raise error if attempting to modify a frozen router."""
        if self._frozen:
            raise ConfigError(
                "Cannot modify router after URLs have been generated. "
                "Routers become frozen when api.urls is accessed."
            )

    def _api_operation_from_options(
        self, methods: list[str], path: str, options: _OperationOptions
    ) -> Callable[[TCallable], TCallable]:
        return self.api_operation(methods, path, **options.as_kwargs())

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
        return self._api_operation_from_options(
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
        return self._api_operation_from_options(
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
        return self._api_operation_from_options(
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
        return self._api_operation_from_options(
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
        return self._api_operation_from_options(
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
        options = _OperationOptions(
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
        )

        def decorator(view_func: TCallable) -> TCallable:
            self.add_api_operation(path, methods, view_func, **options.as_kwargs())
            return view_func

        return decorator

    def add_api_operation(
        self,
        path: str,
        methods: list[str],
        view_func: Callable,
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
    ) -> None:
        self._check_not_frozen()
        path = re.sub(r"\{uuid:(\w+)\}", r"{uuidstr:\1}", path, flags=re.IGNORECASE)
        # django by default convert strings to UUIDs
        # but we want to keep them as strings to let pydantic handle conversion/validation
        # if user whants UUID object
        # uuidstr is custom registered converter

        # No decoration here - will be done in build_routers

        if path not in self.path_operations:
            path_view = PathView()
            self.path_operations[path] = path_view
        else:
            path_view = self.path_operations[path]

        by_alias = self.by_alias if by_alias is None else by_alias
        exclude_unset = self.exclude_unset if exclude_unset is None else exclude_unset
        exclude_defaults = (
            self.exclude_defaults if exclude_defaults is None else exclude_defaults
        )
        exclude_none = self.exclude_none if exclude_none is None else exclude_none

        path_view.add_operation(
            path=path,
            methods=methods,
            view_func=view_func,
            auth=auth,
            operation_id=operation_id,
            summary=summary,
            description=description,
            tags=tags,
            deprecated=deprecated,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            url_name=url_name,
            include_in_schema=include_in_schema,
            openapi_extra=openapi_extra,
        )
        # Note: API binding is now done via BoundRouter when urls are generated

        return None

    def urls_paths(
        self, prefix: str, api: "HattoriAPI | None" = None
    ) -> collections.abc.Iterator[URLPattern]:
        """
        Generate URL patterns for this router.

        Note: This method is primarily for internal use. For mounting routers to APIs,
        use HattoriAPI.add_router() which handles proper binding via BoundRouter.

        Args:
            prefix: URL prefix for all paths
            api: Optional API instance for generating URL names (for backward compat)
        """
        # Ensure decorators are applied before generating URLs
        self._apply_decorators_to_operations()

        prefix = replace_path_param_notation(prefix)
        for path, path_view in self.path_operations.items():
            for operation in path_view.operations:
                path = replace_path_param_notation(path)
                route = "/".join([i for i in (prefix, path) if i])
                # to skip lot of checks we simply treat double slash as a mistake:
                route = normalize_path(route)
                route = route.lstrip("/")

                url_name = getattr(operation, "url_name", "")
                if not url_name and api:
                    url_name = api.get_operation_url_name(operation, router=self)

                yield django_path(route, path_view.get_view(), name=url_name)

    def add_router(
        self,
        prefix: str,
        router: "Router | str",
        *,
        auth: Any = NOT_SET,
        tags: list[str] | None = None,
        url_name_prefix: str | None = None,
    ) -> None:
        self._check_not_frozen()

        if isinstance(router, str):
            router = import_string(router)
            assert isinstance(router, Router)

        existing_templates = {child_router for _, child_router, *_ in self._routers}
        if router in existing_templates and url_name_prefix is None:
            raise ConfigError(
                "Router is already mounted to this parent router. When mounting "
                "the same router multiple times, you must provide unique "
                "url_name_prefix for each mount."
            )

        # Store child router with its mount-time configuration.
        # These values belong to the mount, not the shared router template.
        self._routers.append((prefix, router, auth, tags, url_name_prefix))

    def add_decorator(
        self,
        decorator: Callable,
        mode: DecoratorMode = "operation",
    ) -> None:
        """
        Add a decorator to be applied to all operations in this router.

        Args:
            decorator: The decorator function to apply
            mode: "operation" (default) applies after validation,
                  "view" applies before validation
        """
        self._check_not_frozen()
        if mode not in ("view", "operation"):
            raise ValueError(f"Invalid decorator mode: {mode}")
        self._decorators.append((decorator, mode))

    def build_routers(
        self,
        prefix: str,
        inherited_decorators: list[tuple[Callable, DecoratorMode]] | None = None,
        inherited_auth: Any = NOT_SET,
        inherited_tags: list[str] | None = None,
    ) -> list[RouterMount]:
        """
        Build mount configurations for this router and all child routers.

        This method does NOT mutate any router state - it returns a list of
        RouterMount objects that describe how to bind routers to an API.

        Args:
            prefix: The URL prefix for this router
            inherited_decorators: Decorators inherited from parent routers/API
            inherited_auth: Auth inherited from parent routers
            inherited_tags: Tags inherited from parent routers

        Returns:
            List of RouterMount configurations for this router and all descendants
        """
        if inherited_decorators is None:
            inherited_decorators = []

        # Create mount configuration for this router
        mount = RouterMount(
            template=self,
            prefix=prefix,
            inherited_decorators=list(inherited_decorators),
            inherited_auth=inherited_auth,
            inherited_tags=inherited_tags,
        )

        # Calculate values to pass to children
        child_decorators = inherited_decorators + self._decorators

        # For auth/tags, effective value is used for children:
        # priority: this router's own setting > inherited
        child_auth = self.auth if self.auth is not NOT_SET else inherited_auth
        child_tags = self.tags if self.tags is not None else inherited_tags

        # Build mounts for child routers
        child_mounts: list[RouterMount] = []
        for (
            child_prefix,
            child_router,
            child_mount_auth,
            child_mount_tags,
            child_url_name_prefix,
        ) in self._routers:
            child_path = normalize_path("/".join((prefix, child_prefix))).lstrip("/")
            child_inherited_auth = (
                child_mount_auth if child_mount_auth is not NOT_SET else child_auth
            )
            mounts = child_router.build_routers(
                child_path,
                child_decorators,
                child_inherited_auth,
                child_tags,
            )
            if mounts and child_mount_auth is not NOT_SET:
                mounts[0].auth = child_mount_auth
            # Apply mount-level tags override to the first mount (the child router itself)
            if mounts and child_mount_tags is not None:
                mounts[0].tags = child_mount_tags
            if child_url_name_prefix is not None:
                for child_mount in mounts:
                    child_mount.url_name_prefix = child_url_name_prefix
            child_mounts.extend(mounts)

        return [mount, *child_mounts]

    def _apply_decorators_to_operations(self) -> None:
        """Apply all stored decorators to operations in this router"""
        for path_view in self.path_operations.values():
            for operation in path_view.operations:
                # Track what decorators have already been applied to avoid duplicates
                applied_decorators = getattr(operation, "_applied_decorators", [])

                # Apply decorators that haven't been applied yet
                for decorator, mode in self._decorators:
                    if (decorator, mode) not in applied_decorators:
                        if mode == "view":
                            operation.run = decorator(operation.run)  # type: ignore
                        elif mode == "operation":
                            operation.view_func = decorator(operation.view_func)
                        else:
                            raise ValueError(
                                f"Invalid decorator mode: {mode}"
                            )  # pragma: no cover
                        applied_decorators.append((decorator, mode))

                # Store what decorators have been applied
                operation._applied_decorators = applied_decorators  # type: ignore[attr-defined]
