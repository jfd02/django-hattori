import inspect
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from django.http import HttpRequest

from hattori.responses import APIReturn
from hattori.security.base import parse_api_return_responses
from hattori.utils import is_async_callable

__all__ = ["BasePermission"]


class BasePermission(ABC):
    """Authorization check that runs *after* authentication succeeds.

    Where :class:`~hattori.security.AuthBase` answers "who are you" (and populates
    ``request.auth``), a permission answers "may you do this". Permissions on an
    operation run with **AND** semantics — every one must pass — and each receives
    the already-authenticated ``request.auth`` plus the route's path parameters,
    so resource-scoped checks are possible::

        class IsHouseholdAdmin(BasePermission):
            def check(self, request, household_id) -> bool:
                return Membership.objects.filter(
                    user=request.auth, household_id=household_id, role="admin"
                ).exists()

        @api.get("/households/{household_id}/budget",
                 auth=[JwtAuth()], permissions=[IsHouseholdAdmin()])
        def budget(request, household_id: int) -> BudgetOut:
            ...

    ``check`` may return:

    * ``True`` (or any truthy value) — the check passes, move on.
    * ``False``/``None`` — denied; the framework returns a ``403`` using this
      permission's :attr:`message`.
    * an :class:`~hattori.APIReturn` instance (e.g. a typed ``Forbidden(...)``) —
      short-circuits to that response. Declaring such variants in ``check``'s
      return annotation documents them on every operation's OpenAPI spec, exactly
      like auth-return types::

        class NotAdmin(Forbidden[Literal[Error.NOT_ADMIN]]):
            message = "Admin role required"

        class IsHouseholdAdmin(BasePermission):
            def check(self, request, household_id) -> Literal[True] | NotAdmin:
                if is_admin(request.auth, household_id):
                    return True
                return NotAdmin()

    ``check`` only receives the path parameters it names. Declare exactly the ones
    you need (``check(self, request, household_id)``); add ``**kwargs`` to receive
    all of them, or take none (``check(self, request)``) for a global check. The
    path values are the raw strings from URL routing — coerce as needed.

    ``check`` may be ``async def``; it is awaited natively on async operations and
    run in a threadpool on sync ones (and vice-versa).
    """

    #: Default ``403`` message used when ``check`` returns a falsy value.
    message: str = "Forbidden"

    def __init__(self) -> None:
        signature = inspect.signature(self.check)
        self._accepts_var_keyword = any(
            param.kind is inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )
        self._path_param_names = {
            name
            for name, param in signature.parameters.items()
            if name != "request"
            and param.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        }
        self.is_async = is_async_callable(self.check)
        self.permission_responses: dict[int, Any] = parse_api_return_responses(
            self.check, f"{type(self).__name__}.check"
        )

    @abstractmethod
    def check(
        self, request: HttpRequest, **path_params: Any
    ) -> bool | APIReturn | None:
        pass  # pragma: no cover

    def select_path_kwargs(self, path_params: Mapping[str, Any]) -> dict[str, Any]:
        """Pick the subset of ``path_params`` that ``check`` actually accepts.

        Keeps a permission usable across routes with differing path params: a
        global ``check(self, request)`` gets nothing, a scoped
        ``check(self, request, household_id)`` gets just ``household_id``, and a
        ``**kwargs`` signature gets everything.
        """
        if self._accepts_var_keyword:
            return dict(path_params)
        return {
            name: value
            for name, value in path_params.items()
            if name in self._path_param_names
        }
