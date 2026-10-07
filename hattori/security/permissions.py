import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, get_args, get_origin

from hattori.constants import NOT_SET
from hattori.errors import ConfigError
from hattori.responses import APIReturn
from hattori.security.base import parse_api_return_responses, return_annotation_arms
from hattori.utils import is_async_callable

__all__ = ["BasePermission", "validate_permissions"]


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

    * ``True`` — the check passes, move on.
    * ``False``/``None`` — denied; the framework returns a ``403`` using this
      permission's :attr:`message`. The OpenAPI spec documents that ``403`` on
      every operation the permission guards, unless ``check``'s return
      annotation rules a falsy result out (as ``Literal[True] | NotAdmin`` does
      below).
    * anything else — a :class:`~hattori.errors.ConfigError`. A result that is
      only truthy is not a pass: a method ``check`` forgot to call and a
      ``(False, "reason")`` tuple are truthy too. Spell a truthiness test out
      with ``bool(...)``.
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

    A permission that overrides ``__init__`` (to take a role, say) must call
    ``super().__init__()``, which is where ``check``'s signature is read.
    """

    #: Default ``403`` message used when ``check`` returns ``False`` or ``None``.
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
        self.can_return_falsy: bool = _can_return_falsy(self.check)

    # Declared as (*args, **kwargs) rather than (request, **path_params) so that
    # narrower overrides naming their own path params — the documented pattern
    # above — don't trip the type checkers' override-compatibility rule. Both
    # mypy and pyright treat a `*args: Any, **kwargs: Any` supertype signature as
    # accepting any override, which is exactly the contract here: the framework
    # calls `check` with `request` plus whichever path params it declares.
    @abstractmethod
    def check(self, *args: Any, **kwargs: Any) -> bool | APIReturn | None:
        """Return ``True`` to pass, ``False``/``None`` for a ``403``, or an ``APIReturn``.

        Implementations take ``(self, request)`` plus any subset of the route's
        path parameters — see the class docstring for the full contract.
        """
        ...  # pragma: no cover

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


def _can_return_falsy(check: Callable[..., Any]) -> bool:
    """Whether ``check``'s return annotation leaves room for a falsy result.

    ``False`` or ``None`` is answered with the framework's own ``403``, so this
    answers "is that 403 *impossible*": anything it can't prove truthy counts.
    Only two kinds of arm are provably safe — an ``APIReturn``, which
    short-circuits before the result is read as a verdict, and a ``Literal`` of
    truthy values.
    """
    arms = return_annotation_arms(check)
    if arms is None:
        return True
    for arm in arms:
        origin = get_origin(arm)
        if origin is Literal:
            if not all(get_args(arm)):
                return True
            continue
        api_return = origin or arm
        if not (isinstance(api_return, type) and issubclass(api_return, APIReturn)):
            return True
    return False


def validate_permissions(permissions: Any) -> None:
    """Raise :class:`~hattori.errors.ConfigError` unless ``permissions`` is usable.

    Run wherever permissions are declared, so a misconfiguration fails while the
    API is being assembled rather than as a ``500`` on every request to the
    routes it guards.
    """
    if permissions is None or permissions is NOT_SET:
        return
    if not isinstance(permissions, Sequence):
        permissions = [permissions]
    for permission in permissions:
        if isinstance(permission, type) and issubclass(permission, BasePermission):
            name = permission.__name__
            raise ConfigError(
                f"permissions got the class {name}, not an instance of it. "
                f"Pass {name}() instead."
            )
        if not isinstance(permission, BasePermission):
            raise ConfigError(
                f"permissions must be BasePermission instances, got {permission!r}."
            )
        if not hasattr(permission, "_path_param_names"):
            raise ConfigError(
                f"{type(permission).__name__}.__init__ must call super().__init__()."
            )
