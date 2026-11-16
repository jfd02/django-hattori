import collections.abc
import re
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from typing import (
    TYPE_CHECKING,
    Any,
)

from django.urls import URLPattern
from django.urls import path as django_path
from django.utils.module_loading import import_string

from hattori.constants import NOT_SET
from hattori.decorators import DecoratorMode
from hattori.errors import ConfigError
from hattori.operation import PathView
from hattori.security.permissions import validate_permissions
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

    template: Router
    prefix: str
    url_name_prefix: str | None = None
    auth: Any = NOT_SET
    permissions: Any = NOT_SET
    tags: list[str] | None = None
    inherited_decorators: list[tuple[Callable[..., Any], DecoratorMode]] = field(
        default_factory=list
    )
    # Inherited auth/permissions/tags from parent routers (for nested inheritance)
    inherited_auth: Any = NOT_SET
    inherited_permissions: Any = NOT_SET
    inherited_tags: list[str] | None = None

    # Effective auth/permissions priority:
    # 1. mount override (from the add_router auth/permissions params on this mount)
    # 2. template's own settings (set on the Router itself)
    # 3. inherited from parent (the parent's effective value)
    @property
    def effective_auth(self) -> Any:
        return _first_set(self.auth, self.template.auth, self.inherited_auth)

    @property
    def effective_permissions(self) -> Any:
        return _first_set(
            self.permissions, self.template.permissions, self.inherited_permissions
        )

    # Tags from the add_router call replace the inherited chain and the
    # template's own tags. Otherwise they accumulate: inherited, then own.
    @property
    def effective_tags(self) -> list[str] | None:
        if self.tags is not None:
            return self.tags
        return ((self.inherited_tags or []) + (self.template.tags or [])) or None


def _first_set(*values: Any) -> Any:
    # Falsy values (None, []) are explicit settings; only NOT_SET is skipped.
    for value in values:
        if value is not NOT_SET:
            return value
    return NOT_SET


@dataclass(frozen=True)
class _RouterRegistration:
    """A router passed to add_router(), with the options given for that mount."""

    prefix: str
    router: Router
    auth: Any = NOT_SET
    permissions: Any = NOT_SET
    tags: list[str] | None = None
    url_name_prefix: str | None = None

    def build_mounts(
        self,
        path: str,
        inherited_decorators: list[tuple[Callable[..., Any], DecoratorMode]],
        *,
        inherited_auth: Any = NOT_SET,
        inherited_tags: list[str] | None = None,
        inherited_permissions: Any = NOT_SET,
    ) -> list[RouterMount]:
        mounts = self.router.build_routers(
            path,
            inherited_decorators,
            inherited_auth,
            inherited_tags,
            inherited_permissions,
            mount_auth=self.auth,
            mount_permissions=self.permissions,
            mount_tags=self.tags,
        )
        if self.url_name_prefix is not None:
            for mount in mounts:
                mount.url_name_prefix = self.url_name_prefix
        return mounts


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
    permissions: Any = NOT_SET

    def with_default_auth(self, auth: Any) -> _OperationOptions:
        if self.auth is not NOT_SET:
            return self
        return replace(self, auth=auth)

    def with_default_permissions(self, permissions: Any) -> _OperationOptions:
        if self.permissions is not NOT_SET:
            return self
        return replace(self, permissions=permissions)

    def as_kwargs(self) -> dict[str, Any]:
        # Shallow on purpose: asdict() would deep-copy values such as auth objects.
        return {f.name: getattr(self, f.name) for f in fields(self)}


class BoundRouter:
    """
    A Router template bound to a specific API instance.

    Contains cloned operations with decorators applied. Each mount of a router
    creates a new BoundRouter instance, ensuring complete isolation between mounts.
    """

    def __init__(self, mount: RouterMount, api: HattoriAPI) -> None:
        self.mount = mount
        self.template = mount.template
        self.api = api
        self.prefix = mount.prefix
        self.url_name_prefix = mount.url_name_prefix

        self.auth = mount.effective_auth
        self.permissions = mount.effective_permissions

        self.tags = mount.effective_tags

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

                auth_or_perms_changed = False

                # Apply auth inheritance
                if operation.auth_param == NOT_SET:
                    if self.auth != NOT_SET:
                        operation._set_auth(self.auth)
                        auth_or_perms_changed = True
                    elif self.api.auth != NOT_SET:
                        operation._set_auth(self.api.auth)
                        auth_or_perms_changed = True

                # Apply permission inheritance (same priority as auth)
                if operation.permissions_param == NOT_SET:
                    if self.permissions != NOT_SET:
                        operation._set_permissions(self.permissions)
                        auth_or_perms_changed = True
                    elif self.api.permissions != NOT_SET:
                        operation._set_permissions(self.api.permissions)
                        auth_or_perms_changed = True

                # Inherited auth/permissions can declare typed APIReturn responses;
                # response_models was built in __init__ before they were attached,
                # so rebuild it now to fold those responses into both the spec and
                # the runtime short-circuit dispatch.
                if auth_or_perms_changed:
                    operation._build_response_models()

                # Apply tags inheritance
                if operation.tags is None and self.tags is not None:
                    operation.tags = self.tags

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
        return _url_patterns(
            prefix,
            self.path_operations,
            self.api,
            self.template,
            self.url_name_prefix,
        )


def _url_patterns(
    prefix: str,
    path_operations: dict[str, PathView],
    api: HattoriAPI | None,
    router: Router,
    url_name_prefix: str | None = None,
) -> collections.abc.Iterator[URLPattern]:
    # An operation without an explicit url_name is named by the API; without
    # an API it is left unnamed.
    prefix = replace_path_param_notation(prefix)
    for path, path_view in path_operations.items():
        route = "/".join([i for i in (prefix, replace_path_param_notation(path)) if i])
        # to skip lot of checks we simply treat double slash as a mistake:
        route = normalize_path(route).lstrip("/")

        for operation in path_view.operations:
            url_name = getattr(operation, "url_name", "")
            if not url_name and api is not None:
                url_name = api.get_operation_url_name(operation, router=router)
                if url_name_prefix and url_name:
                    url_name = f"{url_name_prefix}_{url_name}"

            yield django_path(route, path_view.get_view(), name=url_name)


class _HTTPMethodDecorators:
    """
    The per-method operation decorators shared by HattoriAPI and Router.

    Each one dispatches to _api_operation_from_options(), which the subclass
    implements to register the operation.
    """

    def _api_operation_from_options(
        self, methods: list[str], path: str, options: _OperationOptions
    ) -> Callable[[TCallable], TCallable]:
        raise NotImplementedError  # pragma: no cover

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
                permissions=permissions,
            ),
        )


class Router(_HTTPMethodDecorators):
    def __init__(
        self,
        *,
        auth: Any = NOT_SET,
        permissions: Any = NOT_SET,
        tags: list[str] | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool | None = None,
        exclude_defaults: bool | None = None,
        exclude_none: bool | None = None,
    ) -> None:
        self._frozen = False
        self.auth = auth
        validate_permissions(permissions)
        self.permissions = permissions
        self.tags = tags
        self.by_alias = by_alias
        self.exclude_unset = exclude_unset
        self.exclude_defaults = exclude_defaults
        self.exclude_none = exclude_none

        self.path_operations: dict[str, PathView] = {}
        self._routers: list[_RouterRegistration] = []
        self._decorators: list[tuple[Callable[..., Any], DecoratorMode]] = []

    def _freeze(self) -> None:
        """Mark router as frozen - no more modifications allowed."""
        self._frozen = True
        for registration in self._routers:
            registration.router._freeze()

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
            permissions=permissions,
        )

        def decorator(view_func: TCallable) -> TCallable:
            self.add_api_operation(path, methods, view_func, **options.as_kwargs())
            return view_func

        return decorator

    def add_api_operation(
        self,
        path: str,
        methods: list[str],
        view_func: Callable[..., Any],
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
            url_name=url_name,
            include_in_schema=include_in_schema,
            openapi_extra=openapi_extra,
        )
        # Note: API binding is now done via BoundRouter when urls are generated

        return None

    def urls_paths(
        self, prefix: str, api: HattoriAPI | None = None
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

        yield from _url_patterns(prefix, self.path_operations, api, self)

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
        self._check_not_frozen()
        validate_permissions(permissions)

        if isinstance(router, str):
            router = import_string(router)
            assert isinstance(router, Router)

        existing_templates = {registration.router for registration in self._routers}
        if router in existing_templates and url_name_prefix is None:
            raise ConfigError(
                "Router is already mounted to this parent router. When mounting "
                "the same router multiple times, you must provide unique "
                "url_name_prefix for each mount."
            )

        # Store child router with its mount-time configuration.
        # These values belong to the mount, not the shared router template.
        self._routers.append(
            _RouterRegistration(
                prefix=prefix,
                router=router,
                auth=auth,
                permissions=permissions,
                tags=tags,
                url_name_prefix=url_name_prefix,
            )
        )

    def add_decorator(
        self,
        decorator: Callable[..., Any],
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
        inherited_decorators: list[tuple[Callable[..., Any], DecoratorMode]]
        | None = None,
        inherited_auth: Any = NOT_SET,
        inherited_tags: list[str] | None = None,
        inherited_permissions: Any = NOT_SET,
        *,
        mount_auth: Any = NOT_SET,
        mount_permissions: Any = NOT_SET,
        mount_tags: list[str] | None = None,
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
            inherited_permissions: Permissions inherited from parent routers
            mount_auth: Auth override from the add_router() call for this mount
            mount_permissions: Permissions override from the add_router() call
                for this mount
            mount_tags: Tags override from the add_router() call for this mount

        Returns:
            List of RouterMount configurations for this router and all descendants
        """
        if inherited_decorators is None:
            inherited_decorators = []

        # Create mount configuration for this router
        mount = RouterMount(
            template=self,
            prefix=prefix,
            auth=mount_auth,
            permissions=mount_permissions,
            tags=mount_tags,
            inherited_decorators=list(inherited_decorators),
            inherited_auth=inherited_auth,
            inherited_permissions=inherited_permissions,
            inherited_tags=inherited_tags,
        )

        # Calculate values to pass to children
        child_decorators = inherited_decorators + self._decorators

        # Children inherit this router's effective auth/permissions/tags, so a
        # mount-level override reaches descendants just as it reaches this router.
        # Children then add their own tags on top of the inherited ones.
        child_auth = mount.effective_auth
        child_permissions = mount.effective_permissions
        child_tags = mount.effective_tags

        # Build mounts for child routers
        child_mounts: list[RouterMount] = []
        for registration in self._routers:
            child_path = normalize_path("/".join((prefix, registration.prefix))).lstrip(
                "/"
            )
            child_mounts.extend(
                registration.build_mounts(
                    child_path,
                    child_decorators,
                    inherited_auth=child_auth,
                    inherited_tags=child_tags,
                    inherited_permissions=child_permissions,
                )
            )

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
