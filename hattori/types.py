from typing import Any, Callable, TypeVar

__all__ = ["TCallable"]

TCallable = TypeVar("TCallable", bound=Callable[..., Any])


# unfortunately this doesn't work yet, see
# https://github.com/python/mypy/issues/3924
# Decorator = Callable[[TCallable], TCallable]
