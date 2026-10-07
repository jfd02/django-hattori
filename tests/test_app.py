import contextlib
import warnings
from functools import wraps
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.test import RequestFactory

from hattori import SSE, HattoriAPI, Router
from hattori.decorators import decorate_view
from hattori.errors import ConfigError, HttpError
from hattori.testing import TestAsyncClient, TestClient

api = HattoriAPI()

client = TestClient(api)

# TODO: check if you add  operation to the same path - it should raise a ConfigError that this path already exist
# make sure to check how this will work with versioning
# and also check what will happen if you add same path in different routers
#  api.add_router("", router1)
#  api.add_router("", router2)
# and both routers have same path defined


@api.get("")
def emptypath(request) -> str:
    return "/"


@api.get("/get")
def get(request) -> str:
    return f"this is {request.method}"


@api.post("/post")
def post(request) -> str:
    return f"this is {request.method}"


@api.put("/put")
def put(request) -> str:
    return f"this is {request.method}"


@api.patch("/patch")
def patch(request) -> str:
    return f"this is {request.method}"


@api.delete("/delete")
def delete(request) -> str:
    return f"this is {request.method}"


@api.api_operation(["GET", "POST"], "/multi")
def multiple(request) -> str:
    return f"this is {request.method}"


@api.get("/html")
def html(request) -> str:
    return HttpResponse("html")


@api.get("/file")
def file_response(request) -> str:
    tmp = NamedTemporaryFile(delete=False)
    try:
        p = Path(tmp.name)
        p.write_bytes(b"this is a file")
        return FileResponse(Path(tmp.name).open("rb"))
    finally:
        with contextlib.suppress(PermissionError):
            Path(tmp.name).unlink()


@pytest.mark.parametrize(
    "method,path,expected_status,expected_data,expected_streaming",
    [
        ("get", "/", 200, "/", False),
        ("get", "/get", 200, "this is GET", False),
        ("post", "/post", 200, "this is POST", False),
        ("put", "/put", 200, "this is PUT", False),
        ("patch", "/patch", 200, "this is PATCH", False),
        ("delete", "/delete", 200, "this is DELETE", False),
        ("get", "/multi", 200, "this is GET", False),
        ("post", "/multi", 200, "this is POST", False),
        ("patch", "/multi", 405, {"detail": "Method Not Allowed"}, False),
        ("get", "/html", 200, b"html", False),
        ("get", "/file", 200, b"this is a file", True),
    ],
)
def test_method(method, path, expected_status, expected_data, expected_streaming):
    func = getattr(client, method)
    response = func(path)
    assert response.status_code == expected_status
    assert response.streaming == expected_streaming
    try:
        data = response.json()
    except Exception:
        data = response.content
    assert data == expected_data


def test_path_parameter_may_share_a_name_with_the_dispatch_arguments():
    api = HattoriAPI()

    @api.get("/jobs/{operation}")
    def job(request, operation: str) -> str:
        return operation

    assert TestClient(api).get("/jobs/restart").json() == "restart"


def test_undeclared_method_is_answered_for_a_router_mounted_without_binding():
    # Router.urls_paths() binds nothing itself; the operations' API is all a
    # path view has to answer with.
    api = HattoriAPI()
    router = Router()

    @router.get("/thing")
    def thing(request) -> str:
        return "ok"

    for path_view in router.path_operations.values():
        for operation in path_view.operations:
            operation.api = api
    view = next(iter(router.urls_paths("", api=api))).callback

    response = view(RequestFactory().put("/thing"))

    assert response.status_code == 405
    assert response["Allow"] == "GET, HEAD, OPTIONS"
    assert view(RequestFactory().options("/thing")).status_code == 200


def test_method_not_allowed_names_the_allowed_methods():
    response = client.patch("/multi")

    assert response["Content-Type"] == "application/json; charset=utf-8"
    assert response["Allow"] == "GET, POST, HEAD, OPTIONS"
    assert client.get("/post")["Allow"] == "POST, OPTIONS"


def test_head_is_answered_by_the_get_operation():
    response = client.request("HEAD", "/get")

    assert response.status_code == 200
    assert response.json() == "this is HEAD"
    assert client.request("HEAD", "/post").status_code == 405


def test_declared_head_operation_is_not_replaced_by_get():
    api = HattoriAPI()

    @api.get("/resource")
    def read(request) -> str:
        return "GET"

    @api.api_operation(["HEAD"], "/resource")
    def probe(request) -> str:
        return "HEAD"

    client = TestClient(api)

    assert client.request("HEAD", "/resource").json() == "HEAD"
    assert client.put("/resource")["Allow"] == "GET, HEAD, OPTIONS"


def test_head_does_not_start_a_stream():
    api = HattoriAPI()
    started = []

    @api.get("/events")
    def events(request) -> SSE[str]:
        started.append(True)
        yield "tick"

    response = TestClient(api).request("HEAD", "/events")

    assert response.status_code == 405
    assert response["Allow"] == "GET, OPTIONS"
    assert started == []


def test_head_does_not_pull_a_hand_built_stream():
    api = HattoriAPI()
    pulled = []

    def chunks():
        pulled.append(True)
        yield b"chunk"

    @api.get("/download")
    def download(request) -> str:
        response = StreamingHttpResponse(chunks(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="rows.csv"'
        return response

    client = TestClient(api)
    response = client.request("HEAD", "/download")

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert response["Content-Disposition"] == 'attachment; filename="rows.csv"'
    assert response.content == b""
    assert pulled == []
    assert client.get("/download").content == b"chunk"


def test_head_does_not_pull_a_stream_an_exception_handler_answers_with():
    api = HattoriAPI()
    pulled = []

    def chunks():
        pulled.append(True)
        yield b"chunk"

    @api.exception_handler(PermissionDenied)
    def streamed(request, exc):
        return StreamingHttpResponse(chunks(), status=403)

    def denies(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            raise PermissionDenied

        return wrapper

    @api.get("/limited")
    @decorate_view(denies)
    def limited(request) -> str:
        return "ok"

    response = TestClient(api).request("HEAD", "/limited")

    assert response.status_code == 403
    assert response.content == b""
    assert pulled == []


def test_head_does_not_pull_a_streamed_error_wherever_it_comes_from():
    api = HattoriAPI()
    pulled = []

    def chunks():
        pulled.append(True)
        yield b"chunk"

    @api.exception_handler(HttpError)
    def streamed(request, exc):
        return StreamingHttpResponse(chunks(), status=exc.status_code)

    @api.post("/write-only")
    def write_only(request) -> str:
        return "ok"

    @api.get("/events")
    def events(request) -> SSE[str]:
        yield "tick"

    client = TestClient(api)
    method_not_allowed = client.request("HEAD", "/write-only")
    declared_stream = client.request("HEAD", "/events")
    root = client.request("HEAD", "/")

    assert method_not_allowed.status_code == 405
    assert declared_stream.status_code == 405
    assert root.status_code == 404
    assert pulled == []
    # A request that does want the body still gets it.
    assert client.get("/write-only").content == b"chunk"


@pytest.mark.django_db  # closing a response signals request_finished
def test_head_does_not_read_a_file_response(tmp_path):
    api = HattoriAPI()
    report = tmp_path / "report.txt"
    report.write_bytes(b"0123456789")
    opened = []

    @api.get("/report")
    def download(request) -> str:
        opened.append(report.open("rb"))
        return FileResponse(opened[-1])

    response = TestClient(api).request("HEAD", "/report")

    assert response["Content-Length"] == "10"
    assert response.content == b""
    # Not read, and still closed along with the response.
    assert opened[0].tell() == 0
    response.close()
    assert opened[0].closed


@pytest.mark.asyncio
async def test_head_does_not_pull_an_async_hand_built_stream():
    api = HattoriAPI()
    pulled = []

    async def chunks():
        pulled.append(True)
        yield b"chunk"

    @api.get("/download")
    async def download(request) -> str:
        return StreamingHttpResponse(chunks(), content_type="text/csv")

    # Iterated as ASGI does, where Django warns about the wrong kind of stream.
    func, request, kwargs = TestAsyncClient(api)._resolve("HEAD", "/download", {}, {})
    response = await func(request, **kwargs)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        chunks = [chunk async for chunk in response]

    assert response.status_code == 200
    assert chunks == []
    assert pulled == []


def test_options_lists_the_methods_without_running_an_operation():
    response = client.request("OPTIONS", "/multi")

    assert response.status_code == 200
    assert response.content == b""
    assert response["Content-Length"] == "0"
    assert response["Allow"] == "GET, POST, HEAD, OPTIONS"


def test_declared_options_operation_is_not_replaced():
    api = HattoriAPI()

    @api.api_operation(["OPTIONS"], "/resource")
    def describe(request) -> str:
        return "described"

    client = TestClient(api)

    assert client.request("OPTIONS", "/resource").json() == "described"
    assert client.get("/resource")["Allow"] == "OPTIONS"


def test_validates():
    # Registry check was removed - routers are now independent templates
    # that can be reused across multiple APIs without conflicts
    # This test now just verifies that creating an API and accessing urls works
    api2 = HattoriAPI(urls_namespace="test-validates-api")
    _ = api2.urls  # Should not raise


def test_duplicate_method_on_same_path_raises_config_error():
    duplicate_api = HattoriAPI(urls_namespace="duplicate-method")

    @duplicate_api.get("/duplicate")
    def first(request) -> str:
        return "first"

    with pytest.raises(ConfigError):

        @duplicate_api.get("/duplicate")
        def second(request) -> str:
            return "second"
