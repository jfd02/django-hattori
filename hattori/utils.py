import inspect
import re
from typing import Any, Callable

from django.http import HttpRequest, HttpResponseForbidden
from django.middleware.csrf import CsrfViewMiddleware

__all__ = [
    "check_csrf",
    "normalize_path",
    "contribute_operation_callback",
]


def replace_path_param_notation(path: str) -> str:
    return path.replace("{", "<").replace("}", ">")


def normalize_path(path: str) -> str:
    return re.sub(r"/+", "/", path)


def _no_view() -> None:
    pass  # pragma: no cover


def check_csrf(
    request: HttpRequest, callback: Callable = _no_view
) -> HttpResponseForbidden | None:
    mware = CsrfViewMiddleware(lambda x: HttpResponseForbidden())  # pragma: no cover
    request.csrf_processing_done = False  # type: ignore
    mware.process_request(request)
    return mware.process_view(request, callback, (), {})


def is_async_callable(f: Callable[..., Any]) -> bool:
    return inspect.iscoroutinefunction(f) or inspect.iscoroutinefunction(
        getattr(f, "__call__", None)
    )


def is_optional_type(t: type[Any]) -> bool:
    try:
        return type(None) in t.__args__
    except AttributeError:
        return False


def contribute_operation_callback(
    func: Callable[..., Any], callback: Callable[..., Any]
) -> None:
    if not hasattr(func, "_hattori_contribute_to_operation"):
        func._hattori_contribute_to_operation = []  # type: ignore
    func._hattori_contribute_to_operation.append(callback)  # type: ignore


def contribute_operation_args(
    func: Callable[..., Any], arg_name: str, arg_type: type, arg_source: Any
) -> None:
    if not hasattr(func, "_hattori_contribute_args"):
        func._hattori_contribute_args = []  # type: ignore
    func._hattori_contribute_args.append((arg_name, arg_type, arg_source))  # type: ignore
