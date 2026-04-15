"""
Tests for router reuse (template/instance architecture).

These tests verify that:
1. Routers can be mounted to multiple APIs
2. Routers can be mounted multiple times with url_name_prefix
3. Decorators are isolated between mounts
4. Freeze mechanism works correctly
5. Operation.clone() copies all attributes
"""


import pytest

from hattori import HattoriAPI, Router, Schema
from hattori.constants import NOT_SET
from hattori.errors import ConfigError
from hattori.operation import Operation, PathView
from hattori.security import APIKeyQuery
from hattori.testing import TestClient


class IdResult(Schema):
    id: int


class SourceResult(Schema):
    source: str


class OkResult(Schema):
    ok: bool


class BoolTestResult(Schema):
    test: bool


class MethodResult(Schema):
    method: str


class AuthValueResult(Schema):
    auth: str


class QueryAuth(APIKeyQuery):
    def __init__(self, secret: str):
        self.secret = secret
        super().__init__()

    def authenticate(self, request, key):
        if key == self.secret:
            return key


class TestRouterReuse:
    """Test that routers can be reused across multiple APIs."""

    def test_same_router_different_apis(self):
        """Same router can be mounted to different HattoriAPI instances."""
        router = Router(tags=["shared"])

        @router.get("/items")
        def list_items(request) -> list[IdResult]:
            return [{"id": 1}]

        api_v1 = HattoriAPI(version="1.0", urls_namespace="api-v1")
        api_v1.add_router("/v1", router)

        api_v2 = HattoriAPI(version="2.0", urls_namespace="api-v2")
        api_v2.add_router("/v2", router)

        # Both APIs should work
        client_v1 = TestClient(api_v1)
        client_v2 = TestClient(api_v2)

        response_v1 = client_v1.get("/v1/items")
        response_v2 = client_v2.get("/v2/items")

        assert response_v1.status_code == 200
        assert response_v2.status_code == 200
        assert response_v1.json() == [{"id": 1}]
        assert response_v2.json() == [{"id": 1}]

    def test_same_router_multiple_mounts_requires_url_name_prefix(self):
        """Mounting same router twice without url_name_prefix raises ConfigError."""
        router = Router()

        @router.get("/items")
        def list_items(request) -> list[IdResult]:
            return []

        api = HattoriAPI()
        api.add_router("/v1", router)

        with pytest.raises(ConfigError, match="url_name_prefix"):
            api.add_router("/v2", router)

    def test_same_router_multiple_mounts_with_url_name_prefix(self):
        """Same router can be mounted multiple times with different url_name_prefix."""
        router = Router()

        @router.get("/items")
        def list_items(request) -> list[SourceResult]:
            return [{"source": "shared"}]

        api = HattoriAPI(urls_namespace="multi-mount")
        api.add_router("/admin", router, url_name_prefix="admin")
        api.add_router("/public", router, url_name_prefix="public")

        client = TestClient(api)

        response_admin = client.get("/admin/items")
        response_public = client.get("/public/items")

        assert response_admin.status_code == 200
        assert response_public.status_code == 200


class TestDecoratorIsolation:
    """Test that decorators are isolated between mounts."""

    def test_decorators_not_shared_between_apis(self):
        """Decorators added to one API don't affect the same router on another API."""
        router = Router()
        calls = {"api1": 0, "api2": 0}

        @router.get("/test")
        def test_endpoint(request) -> OkResult:
            return {"ok": True}

        def api1_decorator(func):
            def wrapper(*args, **kwargs):
                calls["api1"] += 1
                return func(*args, **kwargs)

            return wrapper

        def api2_decorator(func):
            def wrapper(*args, **kwargs):
                calls["api2"] += 1
                return func(*args, **kwargs)

            return wrapper

        api1 = HattoriAPI(urls_namespace="iso-api1")
        api1.add_decorator(api1_decorator)
        api1.add_router("", router)

        api2 = HattoriAPI(urls_namespace="iso-api2")
        api2.add_decorator(api2_decorator)
        api2.add_router("", router)

        client1 = TestClient(api1)
        client2 = TestClient(api2)

        # Reset calls
        calls["api1"] = 0
        calls["api2"] = 0

        client1.get("/test")
        assert calls["api1"] == 1
        assert calls["api2"] == 0

        client2.get("/test")
        assert calls["api1"] == 1
        assert calls["api2"] == 1


class TestNestedRouterMountIsolation:
    """Test that nested router mount overrides are isolated per mount."""

    def test_nested_child_mount_auth_does_not_leak_between_parents(self):
        child = Router()

        @child.get("/items")
        def list_items(request) -> AuthValueResult:
            return {"auth": request.auth}

        parent_one = Router()
        parent_one.add_router("/child", child, auth=QueryAuth("one"), url_name_prefix="one")

        parent_two = Router()
        parent_two.add_router("/child", child, auth=QueryAuth("two"), url_name_prefix="two")

        api = HattoriAPI(urls_namespace="nested-auth-isolation")
        api.add_router("/one", parent_one)
        api.add_router("/two", parent_two)

        client = TestClient(api)

        assert client.get("/one/child/items?key=one").status_code == 200
        assert client.get("/one/child/items?key=two").status_code == 401
        assert client.get("/two/child/items?key=one").status_code == 401
        assert client.get("/two/child/items?key=two").status_code == 200

    def test_nested_same_child_multiple_mounts_requires_url_name_prefix(self):
        parent = Router()
        child = Router()

        @child.get("/items")
        def list_items(request) -> OkResult:
            return {"ok": True}

        parent.add_router("/one", child)

        with pytest.raises(ConfigError, match="url_name_prefix"):
            parent.add_router("/two", child)

    def test_nested_same_child_multiple_mounts_with_url_name_prefix(self):
        parent = Router()
        child = Router()

        @child.get("/items")
        def list_items(request) -> OkResult:
            return {"ok": True}

        parent.add_router("/one", child, url_name_prefix="one")
        parent.add_router("/two", child, url_name_prefix="two")

        api = HattoriAPI(urls_namespace="nested-name-prefix")
        api.add_router("/parent", parent)

        names = {
            pattern.name for pattern in api.urls[0] if getattr(pattern, "name", None)
        }

        assert "one_list_items" in names
        assert "two_list_items" in names

    def test_duplicate_url_names_raise_during_url_generation(self):
        api = HattoriAPI(urls_namespace="duplicate-names")
        router_one = Router()
        router_two = Router()

        @router_one.get("/one", url_name="shared")
        def op_one(request) -> OkResult:
            return {"ok": True}

        @router_two.get("/two", url_name="shared")
        def op_two(request) -> OkResult:
            return {"ok": True}

        api.add_router("/a", router_one)
        api.add_router("/b", router_two)

        with pytest.raises(ConfigError, match="Duplicate URL name 'shared'"):
            _ = api.urls

class TestFreezeBehavior:
    """Test that routers are frozen after URLs are generated."""

    def test_router_frozen_after_urls_accessed(self):
        """Router becomes frozen after api.urls is accessed."""
        router = Router()

        @router.get("/items")
        def list_items(request) -> list[str]:
            return []

        api = HattoriAPI(urls_namespace="freeze-test")
        api.add_router("", router)

        # Access urls (triggers freezing)
        _ = api.urls

        # Trying to add more operations should fail
        with pytest.raises(ConfigError, match="frozen"):

            @router.get("/new")
            def new_endpoint(request) -> list[str]:
                return []

    def test_cannot_add_router_after_urls_accessed(self):
        """Cannot add routers after URLs have been generated."""
        api = HattoriAPI(urls_namespace="freeze-add-router")

        # Access urls
        _ = api.urls

        router = Router()

        with pytest.raises(ConfigError, match="Cannot add routers"):
            api.add_router("/new", router)

    def test_cannot_add_decorator_to_frozen_router(self):
        """Cannot add decorator to frozen router."""
        router = Router()

        @router.get("/items")
        def list_items(request) -> list[str]:
            return []

        api = HattoriAPI(urls_namespace="freeze-decorator")
        api.add_router("", router)

        # Access urls (triggers freezing)
        _ = api.urls

        def some_decorator(func):
            return func

        with pytest.raises(ConfigError, match="frozen"):
            router.add_decorator(some_decorator)


class TestOperationClone:
    """Test Operation.clone() method completeness."""

    def test_clone_copies_all_attributes(self):
        """clone() should copy all essential attributes from original operation."""

        def dummy_view(request) -> BoolTestResult:
            return {"test": True}

        original = Operation(
            path="/test/{id}",
            methods=["GET", "POST"],
            view_func=dummy_view,
            operation_id="test_op",
            summary="Test Summary",
            description="Test Description",
            tags=["tag1", "tag2"],
            deprecated=True,
            by_alias=True,
            exclude_unset=True,
            exclude_defaults=True,
            exclude_none=True,
            include_in_schema=False,
            url_name="custom_url_name",
            openapi_extra={"x-custom": "value"},
        )

        cloned = original.clone()

        # Verify all key attributes are copied
        assert cloned.path == original.path
        assert cloned.methods == original.methods
        assert cloned.methods is not original.methods  # Should be a copy
        assert cloned.view_func is original.view_func  # Same callable reference
        assert cloned.operation_id == original.operation_id
        assert cloned.summary == original.summary
        assert cloned.description == original.description
        assert cloned.tags == original.tags
        assert cloned.tags is not original.tags  # Should be a copy
        assert cloned.deprecated == original.deprecated
        assert cloned.by_alias == original.by_alias
        assert cloned.exclude_unset == original.exclude_unset
        assert cloned.exclude_defaults == original.exclude_defaults
        assert cloned.exclude_none == original.exclude_none
        assert cloned.include_in_schema == original.include_in_schema
        assert cloned.url_name == original.url_name
        assert cloned.openapi_extra == original.openapi_extra
        assert cloned.openapi_extra is not original.openapi_extra  # Should be a copy

        # These should be references (immutable after creation)
        assert cloned.signature is original.signature
        assert cloned.models is original.models

        # Response models should be copied
        assert cloned.response_models == original.response_models
        assert cloned.response_models is not original.response_models

        # API should be None on clone (not bound yet)
        assert cloned.api is None

    def test_clone_preserves_auth_settings(self):
        """clone() should preserve auth settings."""

        def auth_callback(request):
            return True

        def dummy_view(request) -> BoolTestResult:
            return {"test": True}

        original = Operation(
            path="/test",
            methods=["GET"],
            view_func=dummy_view,
            auth=[auth_callback],
        )

        cloned = original.clone()

        assert cloned.auth_param == original.auth_param
        assert cloned.auth_callbacks == original.auth_callbacks
        assert cloned.auth_callbacks is not original.auth_callbacks

    def test_pathview_clone(self):
        """PathView.clone() should clone all contained operations."""
        pv = PathView()

        def view1(request) -> MethodResult:
            return {"method": "get"}

        def view2(request, data: dict) -> MethodResult:
            return {"method": "post"}

        pv.add_operation("/test", ["GET"], view1)
        pv.add_operation("/test", ["POST"], view2)

        cloned = pv.clone()

        assert len(cloned.operations) == len(pv.operations)
        assert cloned.is_async == pv.is_async
        assert cloned.url_name == pv.url_name

        # Operations should be cloned, not same instances
        for orig_op, clone_op in zip(pv.operations, cloned.operations):
            assert orig_op is not clone_op
            assert clone_op.path == orig_op.path
            assert clone_op.methods == orig_op.methods

    def test_find_operation_uses_method_map(self):
        """_find_operation should return the correct operation by HTTP method."""
        from django.test import RequestFactory

        pv = PathView()
        factory = RequestFactory()

        def get_view(request) -> MethodResult:
            return {"method": "get"}

        def post_view(request) -> MethodResult:
            return {"method": "post"}

        pv.add_operation("/test", ["GET"], get_view)
        pv.add_operation("/test", ["POST"], post_view)

        get_op = pv._find_operation(factory.get("/test"))
        assert get_op is not None
        assert "GET" in get_op.methods

        post_op = pv._find_operation(factory.post("/test"))
        assert post_op is not None
        assert "POST" in post_op.methods

        put_op = pv._find_operation(factory.put("/test"))
        assert put_op is None

    def test_cloned_pathview_find_operation(self):
        """Cloned PathView should have a working _method_map."""
        from django.test import RequestFactory

        pv = PathView()
        factory = RequestFactory()

        def get_view(request) -> MethodResult:
            return {"method": "get"}

        pv.add_operation("/test", ["GET", "HEAD"], get_view)

        cloned = pv.clone()

        # Cloned PathView should find operations by method
        get_op = cloned._find_operation(factory.get("/test"))
        assert get_op is not None
        assert "GET" in get_op.methods

        # Cloned operations should be independent from originals
        assert get_op is not pv.operations[0]


class TestCloneCompleteness:
    """Test that clone() is updated when new attributes are added to Operation."""

    def test_clone_attribute_completeness(self):
        """
        Verify that all instance attributes set in __init__ are handled by clone().

        This test helps catch cases where new attributes are added to Operation
        but clone() is not updated accordingly.
        """

        def dummy_view(request) -> OkResult:
            return {}

        # Create operation with all parameters
        op = Operation(
            path="/test",
            methods=["GET"],
            view_func=dummy_view,
        )

        cloned = op.clone()

        # Get all instance attributes that should be present
        # These are the key attributes that clone() should handle
        expected_attrs = [
            "is_async",
            "path",
            "methods",
            "view_func",
            "csrf_exempt",
            "auth_param",
            "auth_callbacks",
            "signature",
            "models",
            "response_models",
            "operation_id",
            "summary",
            "description",
            "tags",
            "deprecated",
            "include_in_schema",
            "openapi_extra",
            "by_alias",
            "exclude_unset",
            "exclude_defaults",
            "exclude_none",
        ]

        for attr in expected_attrs:
            assert hasattr(cloned, attr), f"clone() missing attribute: {attr}"

            # For mutable types, ensure they're copies (not same reference)
            orig_val = getattr(op, attr)
            clone_val = getattr(cloned, attr)

            if isinstance(orig_val, (list, dict)) and orig_val:
                assert clone_val is not orig_val, f"clone() should copy {attr}"

    def test_clone_catches_new_attributes(self):
        """
        IMPORTANT: This test will FAIL if you add a new attribute to Operation
        but forget to handle it in clone().

        When this test fails, update Operation.clone() to handle the new attribute,
        then add it to the KNOWN_ATTRIBUTES set below.
        """

        def dummy_view(request) -> OkResult:
            return {}

        op = Operation(
            path="/test/{id}",
            methods=["GET", "POST"],
            view_func=dummy_view,
            auth=lambda r: True,
            tags=["test"],
            summary="Test summary",
            description="Test description",
            operation_id="test_op",
            deprecated=True,
            by_alias=True,
            exclude_unset=True,
            exclude_defaults=True,
            exclude_none=True,
            url_name="test_url",
            include_in_schema=True,
            openapi_extra={"x-custom": "value"},
        )

        cloned = op.clone()

        # Attributes that are known and handled by clone()
        # If you add a new attribute to Operation, you MUST:
        # 1. Add it to clone() method
        # 2. Add it to this set
        KNOWN_ATTRIBUTES = {
            # Core operation attributes
            "is_async",
            "path",
            "methods",
            "view_func",
            "api",
            # Auth/security
            "csrf_exempt",
            "auth_param",
            "auth_callbacks",
            # Signature and models
            "signature",
            "models",
            "response_models",
            "_resp_annotations",
            # Streaming
            "stream_format",
            "stream_item_model",
            # OpenAPI metadata
            "operation_id",
            "summary",
            "description",
            "tags",
            "deprecated",
            "include_in_schema",
            "openapi_extra",
            "url_name",
            # Response serialization options
            "by_alias",
            "exclude_unset",
            "exclude_defaults",
            "exclude_none",
        }

        # Attributes that are intentionally not cloned (internal/runtime state)
        EXCLUDED_ATTRIBUTES = {
            "_run_decorators",  # Re-applied during clone, not copied
            "_applied_decorators",  # Router-level decorator tracking
        }

        # Get all instance attributes from the original operation
        original_attrs = {
            attr
            for attr in dir(op)
            if not attr.startswith("_")  # Skip private/dunder
            and not callable(getattr(op, attr))  # Skip methods
            and not isinstance(
                getattr(type(op), attr, None), property
            )  # Skip properties
        }

        # Also check for underscore attributes that we explicitly track
        for attr in dir(op):
            if attr.startswith("_") and not attr.startswith("__"):
                if attr in EXCLUDED_ATTRIBUTES:
                    continue
                if hasattr(op, attr) and not callable(getattr(op, attr)):
                    original_attrs.add(attr)

        # Find any attributes that exist on original but aren't in our known set
        unknown_attrs = original_attrs - KNOWN_ATTRIBUTES - EXCLUDED_ATTRIBUTES

        if unknown_attrs:
            raise AssertionError(
                f"New attribute(s) found on Operation that may not be handled by clone(): "
                f"{unknown_attrs}\n\n"
                f"If you added a new attribute to Operation:\n"
                f"1. Update Operation.clone() to handle it\n"
                f"2. Add it to KNOWN_ATTRIBUTES in this test\n"
                f"3. Add it to EXCLUDED_ATTRIBUTES if it should NOT be cloned"
            )

        # Verify all known attributes exist on both original and clone
        for attr in KNOWN_ATTRIBUTES:
            assert hasattr(
                op, attr
            ), f"KNOWN_ATTRIBUTES lists '{attr}' but Operation doesn't have it"
            assert hasattr(cloned, attr), f"clone() doesn't set attribute: {attr}"


class TestTagsInheritance:
    """Test tags inheritance in BoundRouter."""

    def test_tags_inheritance_from_parent(self):
        """Child router inherits tags from parent if not set."""
        parent = Router(tags=["parent-tag"])
        child = Router()

        @child.get("/test")
        def test_op(request) -> OkResult:
            return {"ok": True}

        parent.add_router("/child", child)

        api = HattoriAPI(urls_namespace="tags-inherit-test")
        api.add_router("/parent", parent)
        _ = api.urls

        # Find the child's bound router
        bound_routers = api._get_bound_routers()
        child_bound = next(b for b in bound_routers if b.template is child)
        assert child_bound.tags == ["parent-tag"]

    def test_tags_accumulation(self):
        """
        Test for issue #794: Tags from parent routers should accumulate with child tags.

        When a child router has its own tags, they should be combined with parent tags,
        not replace them.
        """
        parent = Router(tags=["Parent Tag"])
        child = Router(tags=["Child Tag"])

        @child.get("/test")
        def test_op(request) -> OkResult:
            return {"ok": True}

        parent.add_router("/child", child)

        api = HattoriAPI()
        api.add_router("/", parent)

        # Check OpenAPI schema
        schema = api.get_openapi_schema(path_prefix="")
        path_info = schema["paths"]["/child/test"]["get"]

        # Tags should include BOTH parent and child tags
        tags = path_info.get("tags", [])
        assert "Parent Tag" in tags, f"Parent tag missing. Got: {tags}"
        assert "Child Tag" in tags, f"Child tag missing. Got: {tags}"


class TestRouterUrlsPathsMethod:
    """Test Router.urls_paths() method for backward compatibility."""

    def test_urls_paths_generates_patterns(self):
        """Router.urls_paths() should generate URL patterns."""
        router = Router()

        @router.get("/test")
        def test_op(request) -> OkResult:
            return {"ok": True}

        patterns = list(router.urls_paths("prefix"))
        assert len(patterns) == 1
        assert "prefix/test" in str(patterns[0].pattern)

    def test_urls_paths_with_api(self):
        """Router.urls_paths() with API generates proper URL names."""
        router = Router()

        @router.get("/test")
        def test_op(request) -> OkResult:
            return {"ok": True}

        api = HattoriAPI(urls_namespace="urls-paths-test")
        patterns = list(router.urls_paths("prefix", api=api))
        assert len(patterns) == 1
        assert patterns[0].name == "test_op"


class TestRouterAddRouterStringImport:
    """Test Router.add_router() with string import path."""

    def test_add_router_direct(self):
        """Router.add_router() should accept Router instance."""
        parent = Router()
        child = Router()
        parent.add_router("/test", child)

        assert len(parent._routers) == 1
        (
            _,
            added_child,
            mount_auth,
            mount_tags,
            mount_url_name_prefix,
        ) = parent._routers[0]
        assert isinstance(added_child, Router)
        assert added_child is child
        assert mount_auth is NOT_SET
        assert mount_tags is None  # No tags specified in add_router
        assert mount_url_name_prefix is None


class TestBuildRoutersEdgeCases:
    """Test edge cases in build_routers."""

    def test_build_routers_without_inherited_decorators(self):
        """build_routers() called directly without inherited_decorators."""
        router = Router()

        @router.get("/test")
        def test_op(request) -> OkResult:
            return {"ok": True}

        # Call build_routers directly without inherited_decorators
        mounts = router.build_routers("prefix")
        assert len(mounts) == 1
        assert mounts[0].prefix == "prefix"
        assert mounts[0].inherited_decorators == []


class TestApplyDecoratorsToOperations:
    """Test _apply_decorators_to_operations method."""

    def test_apply_decorators_view_mode(self):
        """Test applying decorators in view mode."""
        router = Router()

        calls = []

        def view_decorator(func):
            def wrapper(*args, **kwargs):
                calls.append("view")
                return func(*args, **kwargs)

            return wrapper

        @router.get("/test")
        def test_op(request) -> OkResult:
            return {"ok": True}

        router.add_decorator(view_decorator, mode="view")

        # Force decorator application
        router._apply_decorators_to_operations()

        # Check decorator was applied
        for path_view in router.path_operations.values():
            for op in path_view.operations:
                assert hasattr(op, "_applied_decorators")
                assert len(op._applied_decorators) == 1

    def test_apply_decorators_operation_mode(self):
        """Test applying decorators in operation mode."""
        router = Router()

        calls = []

        def op_decorator(func):
            def wrapper(*args, **kwargs):
                calls.append("operation")
                return func(*args, **kwargs)

            return wrapper

        @router.get("/test")
        def test_op(request) -> OkResult:
            return {"ok": True}

        router.add_decorator(op_decorator, mode="operation")

        # Force decorator application
        router._apply_decorators_to_operations()

        # Check decorator was applied
        for path_view in router.path_operations.values():
            for op in path_view.operations:
                assert hasattr(op, "_applied_decorators")
                assert len(op._applied_decorators) == 1

    def test_apply_decorators_idempotent(self):
        """Test that decorators are not applied twice."""
        router = Router()

        calls = []

        def decorator(func):
            def wrapper(*args, **kwargs):
                calls.append("called")
                return func(*args, **kwargs)

            return wrapper

        @router.get("/test")
        def test_op(request) -> OkResult:
            return {"ok": True}

        router.add_decorator(decorator, mode="view")

        # Apply twice - should be idempotent
        router._apply_decorators_to_operations()
        router._apply_decorators_to_operations()

        # Check decorator was only applied once
        for path_view in router.path_operations.values():
            for op in path_view.operations:
                assert len(op._applied_decorators) == 1


class TestBoundRouterOperationModeDecorator:
    """Test BoundRouter with operation mode decorators."""

    def test_operation_mode_decorator_in_bound_router(self):
        """Test that operation mode decorators work in BoundRouter."""
        router = Router()
        calls = []

        def op_decorator(func):
            def wrapper(*args, **kwargs):
                calls.append("operation")
                return func(*args, **kwargs)

            return wrapper

        @router.get("/test")
        def test_op(request) -> OkResult:
            return {"ok": True}

        router.add_decorator(op_decorator, mode="operation")

        api = HattoriAPI(urls_namespace="op-mode-decorator-test")
        api.add_router("", router)

        client = TestClient(api)
        response = client.get("/test")
        assert response.status_code == 200
        assert "operation" in calls


class TestDecorateViewMultipleCalls:
    """Test decorate_view called multiple times."""

    def test_decorate_view_multiple_decorators(self):
        """Test applying multiple view decorators to same operation."""
        from hattori.decorators import decorate_view

        router = Router()
        calls = []

        def decorator1(func):
            def wrapper(*args, **kwargs):
                calls.append("deco1")
                return func(*args, **kwargs)

            return wrapper

        def decorator2(func):
            def wrapper(*args, **kwargs):
                calls.append("deco2")
                return func(*args, **kwargs)

            return wrapper

        @router.get("/test")
        @decorate_view(decorator1)
        @decorate_view(decorator2)
        def test_op(request) -> OkResult:
            return {"ok": True}

        api = HattoriAPI(urls_namespace="multi-decorate-view-test")
        api.add_router("", router)

        client = TestClient(api)
        response = client.get("/test")
        assert response.status_code == 200
        # Both decorators should be called
        assert "deco1" in calls
        assert "deco2" in calls
