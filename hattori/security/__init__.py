from hattori.security.apikey import APIKeyCookie, APIKeyHeader, APIKeyQuery
from hattori.security.http import HttpBasicAuth, HttpBearer
from hattori.security.session import (
    SessionAuth,
    SessionAuthIsStaff,
    SessionAuthSuperUser,
)

__all__ = [
    "APIKeyCookie",
    "APIKeyHeader",
    "APIKeyQuery",
    "HttpBasicAuth",
    "HttpBearer",
    "SessionAuth",
    "SessionAuthSuperUser",
    "django_auth",
    "django_auth_superuser",
    "django_auth_is_staff",
]

django_auth = SessionAuth()
django_auth_superuser = SessionAuthSuperUser()
django_auth_is_staff = SessionAuthIsStaff()
