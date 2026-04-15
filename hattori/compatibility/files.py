from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from asgiref.sync import iscoroutinefunction, sync_to_async
from django.conf import settings
from django.http import HttpRequest
from django.utils.decorators import sync_and_async_middleware

from hattori.conf import settings as hattori_settings
from hattori.params.models import FileModel

FIX_MIDDLEWARE_PATH: str = "hattori.compatibility.files.fix_request_files_middleware"
FIX_METHODS = hattori_settings.FIX_REQUEST_FILES_METHODS


def need_to_fix_request_files(methods: list[str], params_models: list[Any]) -> bool:
    has_files_params = any(
        issubclass(model_class, FileModel) for model_class in params_models
    )
    method_needs_fix = bool(set(methods) & FIX_METHODS)
    middleware_installed = FIX_MIDDLEWARE_PATH in settings.MIDDLEWARE
    return has_files_params and method_needs_fix and not middleware_installed


def _should_fix(request: HttpRequest) -> bool:
    return (
        request.method in FIX_METHODS and request.content_type != "application/json"
    )


@contextmanager
def _swap_method_for_files(request: HttpRequest) -> Iterator[None]:
    initial_method = request.method
    request.method = "POST"
    request.META["REQUEST_METHOD"] = "POST"
    try:
        yield
    finally:
        request.META["REQUEST_METHOD"] = initial_method
        request.method = initial_method


@sync_and_async_middleware
def fix_request_files_middleware(get_response: Any) -> Any:
    """
    This middleware fixes long historical Django behavior where request.FILES is only
    populated for POST requests.
    https://code.djangoproject.com/ticket/12635
    """
    if iscoroutinefunction(get_response):

        async def async_middleware(request: HttpRequest) -> Any:
            if _should_fix(request):
                with _swap_method_for_files(request):
                    await sync_to_async(request._load_post_and_files)()

            return await get_response(request)

        return async_middleware
    else:

        def sync_middleware(request: HttpRequest) -> Any:
            if _should_fix(request):
                with _swap_method_for_files(request):
                    request._load_post_and_files()

            return get_response(request)

        return sync_middleware
