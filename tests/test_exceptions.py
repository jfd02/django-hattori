
import pytest
from django.http import Http404

from hattori import HattoriAPI, Schema
from hattori.testing import TestAsyncClient, TestClient

api = HattoriAPI()


class CustomException(Exception):
    pass


@api.exception_handler(CustomException)
def on_custom_error(request, exc):
    return api.create_response(request, {"custom": True}, status=422)


class Payload(Schema):
    test: int


@api.post("/error/{code}")
def err_thrower(
    request, code: str, payload: Payload = None
) -> None:
    if code == "base":
        raise RuntimeError("test")
    if code == "404":
        raise Http404("test")
    if code == "custom":
        raise CustomException("test")
    return None


client = TestClient(api)


def test_default_handler(settings):
    settings.DEBUG = True

    response = client.post("/error/base")
    assert response.status_code == 500
    assert b"RuntimeError: test" in response.content

    response = client.post("/error/404")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found: test"}

    response = client.post("/error/custom", body="invalid_json")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail.startswith("Cannot parse request body (")

    settings.DEBUG = False
    with pytest.raises(RuntimeError):
        response = client.post("/error/base")

    response = client.post("/error/custom", body="invalid_json")
    assert response.status_code == 400
    assert response.json() == {"detail": "Cannot parse request body"}


@pytest.mark.parametrize(
    "route,status_code,json",
    [
        ("/error/404", 404, {"detail": "Not Found"}),
        ("/error/custom", 422, {"custom": True}),
    ],
)
def test_exceptions(route, status_code, json):
    response = client.post(route)
    assert response.status_code == status_code
    assert response.json() == json


@pytest.mark.asyncio
async def test_asyncio_exceptions():
    api = HattoriAPI()

    @api.get("/error")
    async def thrower(request) -> None:
        raise Http404("test")

    client = TestAsyncClient(api)
    response = await client.get("/error")
    assert response.status_code == 404


def test_no_handlers():
    api = HattoriAPI()
    api._exception_handlers = {}

    @api.get("/error")
    def thrower(request) -> None:
        raise RuntimeError("test")

    client = TestClient(api)

    with pytest.raises(RuntimeError):
        client.get("/error")
