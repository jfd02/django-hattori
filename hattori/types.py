from collections.abc import Callable
from typing import Any, Generic, TypeVar

from django.http import HttpRequest

__all__ = ["TCallable", "AuthedRequest"]

TCallable = TypeVar("TCallable", bound=Callable[..., Any])

AuthT = TypeVar("AuthT")


class AuthedRequest(HttpRequest, Generic[AuthT]):
    """An :class:`~django.http.HttpRequest` whose ``auth`` attribute is typed.

    Successful authentication stashes its result on ``request.auth``, but plain
    ``HttpRequest`` doesn't declare that attribute, so annotating the parameter
    honestly (``request: HttpRequest``) makes every ``request.auth`` read a type
    error. Annotate with this instead, parameterized on whatever the auth class
    returns::

        class JwtAuth(HttpBearer):
            def authenticate(self, request, token: str) -> User | NotAuthenticated:
                ...

        @api.get("/me", auth=JwtAuth())
        def me(request: AuthedRequest[User]) -> UserOut:
            return UserOut(id=request.auth.id)   # request.auth is a User

    Permissions take the same annotation, since they run after authentication::

        class IsHouseholdAdmin(BasePermission):
            def check(self, request: AuthedRequest[User], household_id: str) -> bool:
                return is_admin(request.auth, household_id)

    Annotation-only: the object a view actually receives is Django's own
    ``WSGIRequest``/``ASGIRequest``, never an instance of this class, so don't
    use it with ``isinstance``. Only operations with ``auth=`` set populate
    ``auth`` — annotating an unauthenticated view with it claims an attribute
    that won't be there.
    """

    auth: AuthT
