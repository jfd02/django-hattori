from functools import wraps

from hattori import HattoriAPI
from hattori.decorators import decorate_view
from hattori.testing import TestClient


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
