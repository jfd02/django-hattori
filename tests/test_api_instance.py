
import pytest
from django.urls import path

from hattori import HattoriAPI, Router
from hattori.errors import ConfigError


def test_api_instance():
    """Test that operations are properly bound to API via bound routers."""
    api = HattoriAPI(urls_namespace="api-instance-test")
    router = Router()

    @api.get("/global")
    def global_op(request) -> None:
        return None

    @router.get("/router")
    def router_op(request) -> None:
        return None

    api.add_router("/", router)

    # Access URLs to trigger binding
    _ = api.urls

    # Check via bound routers (the new architecture)
    bound_routers = api._get_bound_routers()
    assert len(bound_routers) == 2  # default + extra

    for bound_router in bound_routers:
        for path_ops in bound_router.path_operations.values():
            for op in path_ops.operations:
                assert op.api is api


def test_reuse_router_requires_url_name_prefix():
    """Mounting same router twice requires url_name_prefix."""
    test_api = HattoriAPI(urls_namespace="reuse-test")
    test_router = Router()

    @test_router.get("/test")
    def test_op(request) -> None:
        return None

    test_api.add_router("/", test_router)

    # Same router mounted again without url_name_prefix should raise
    match = "Router is already mounted"
    with pytest.raises(ConfigError, match=match):
        test_api.add_router("/another-path", test_router)


def test_reuse_router_with_url_name_prefix():
    """Same router can be mounted multiple times with different url_name_prefix."""
    test_api = HattoriAPI(urls_namespace="reuse-prefix-test")
    test_router = Router()

    @test_router.get("/test")
    def test_op(request) -> None:
        return None

    test_api.add_router("/v1", test_router, url_name_prefix="v1")
    test_api.add_router("/v2", test_router, url_name_prefix="v2")

    # Should work - verify URLs are generated
    _ = test_api.urls

    # Both mounts should work
    bound_routers = test_api._get_bound_routers()
    # default router + 2 mounts of test_router
    assert len(bound_routers) == 3


def test_validate_unique_url_names_ignores_non_patterns_and_unnamed_patterns():
    api = HattoriAPI(urls_namespace="unique-name-validation")

    unnamed_pattern = path("unnamed/", lambda request: None)

    # Should ignore non-URLPattern objects and unnamed URLPatterns without raising.
    api._validate_unique_url_names([object(), unnamed_pattern])  # type: ignore[arg-type]
