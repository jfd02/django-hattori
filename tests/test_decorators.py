from functools import wraps

import pytest
from django.core.exceptions import PermissionDenied

from hattori import HattoriAPI, Router
from hattori.decorators import decorate_view
from hattori.testing import TestAsyncClient, TestClient


def some_decorator(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        response = view_func(request, *args)
        response["X-Decorator"] = "some_decorator"
        return response

    return wrapper


def test_decorator_before():
    api = HattoriAPI()

    @decorate_view(some_decorator)
    @api.get("/before")
    def dec_before(request) -> int:
        return 1

    client = TestClient(api)
    response = client.get("/before")
    assert response.status_code == 200
    assert response["X-Decorator"] == "some_decorator"


def test_decorator_after():
    api = HattoriAPI()

    @api.get("/after")
    @decorate_view(some_decorator)
    def dec_after(request) -> int:
        return 1

    client = TestClient(api)
    response = client.get("/after")
    assert response.status_code == 200
    assert response["X-Decorator"] == "some_decorator"


def denies(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        raise PermissionDenied("rate limited")

    return wrapper


def test_exception_raised_by_a_view_decorator_is_answered_by_the_api():
    api = HattoriAPI()

    @api.get("/limited")
    @decorate_view(denies)
    def limited(request) -> int:
        return 1

    response = TestClient(api).get("/limited")

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_exception_raised_by_a_router_view_decorator_is_answered_by_the_api():
    api = HattoriAPI()
    router = Router()
    router.add_decorator(denies, mode="view")

    @router.get("/limited")
    def limited(request) -> int:
        return 1

    api.add_router("/r", router)

    assert TestClient(api).get("/r/limited").json() == {"detail": "Forbidden"}


@pytest.mark.asyncio
async def test_exception_raised_by_an_async_view_decorator_is_answered_by_the_api():
    api = HattoriAPI()

    def denies_async(view_func):
        @wraps(view_func)
        async def wrapper(request, *args, **kwargs):
            raise PermissionDenied

        return wrapper

    @api.get("/limited")
    @decorate_view(denies_async)
    async def limited(request) -> int:
        return 1

    response = await TestAsyncClient(api).get("/limited")

    assert response.status_code == 403


def test_view_decorator_exception_reaches_a_registered_handler():
    api = HattoriAPI()

    class RateLimited(Exception):
        pass

    def limits(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            raise RateLimited

        return wrapper

    @api.exception_handler(RateLimited)
    def slow_down(request, exc):
        return api.create_response(request, {"slow": "down"}, status=429)

    @api.get("/limited")
    @decorate_view(limits)
    def limited(request) -> int:
        return 1

    assert TestClient(api).get("/limited").json() == {"slow": "down"}


def test_exception_no_handler_answers_is_offered_to_the_handlers_once(settings):
    settings.DEBUG = False
    api = HattoriAPI()
    offered = []

    @api.exception_handler(ValueError)
    def decline(request, exc):
        offered.append(exc)
        raise exc

    @api.get("/broken")
    @decorate_view(some_decorator)
    def broken(request) -> int:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        TestClient(api).get("/broken")
    assert len(offered) == 1
