from abc import ABC, abstractmethod
from typing import Any

from django.http import HttpRequest

from hattori.errors import AuthErrorResponse, ConfigError
from hattori.utils import is_async_callable

__all__ = ["SecuritySchema", "AuthBase"]


class SecuritySchema(dict):
    def __init__(self, type: str, **kwargs: Any) -> None:
        super().__init__(type=type, **kwargs)


class AuthBase(ABC):
    openapi_responses: dict[int, type] = {401: AuthErrorResponse}

    def __init__(self) -> None:
        if not hasattr(self, "openapi_type"):
            raise ConfigError("If you extend AuthBase you need to define openapi_type")

        kwargs = {}
        for attr in dir(self):
            if attr.startswith("openapi_") and attr != "openapi_responses":
                name = attr.replace("openapi_", "", 1)
                kwargs[name] = getattr(self, attr)
        self.openapi_security_schema = SecuritySchema(**kwargs)

        self.is_async = False
        if hasattr(self, "authenticate"):  # pragma: no branch
            self.is_async = is_async_callable(self.authenticate)

    @abstractmethod
    def __call__(self, request: HttpRequest) -> Any | None:
        pass  # pragma: no cover
