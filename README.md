# Django Hattori - Fast Django REST Framework

**Django Hattori** is an opinionated fork of [Django Ninja](https://github.com/vitalik/django-ninja), a web framework for building APIs with **Django** and Python **type hints**.

**Documentation**: [https://github.com/jfd02/django-hattori](https://github.com/jfd02/django-hattori)

*Fast to learn, fast to code, fast to run*

**Key features:**

  - **Easy**: Designed to be easy to use and intuitive.
  - **Fast to code**: Type hints and automatic docs lets you focus only on business logic.
  - **Standards-based**: Based on the open standards for APIs: **OpenAPI** (previously known as Swagger) and **JSON Schema**.
  - **Django friendly**: good integration with the Django core and ORM.

---

## Installation

Install directly from the git repo:

```
pip install git+https://github.com/jfd02/django-hattori.git
```

Or pin a specific commit or tag:

```
pip install git+https://github.com/jfd02/django-hattori.git@<sha-or-tag>
```

## Quick Start

Create `api.py` next to your `urls.py`:

```python
from enum import Enum
from typing import TypeAlias

from django.contrib.auth.models import User

from hattori import ApiError, BadRequest, Conflict, Created, HattoriAPI, Schema
from hattori.security import HttpBearer


api = HattoriAPI()


# --- Schemas ---

class SignupIn(Schema):
    username: str
    password: str

class UserOut(Schema):
    id: int
    username: str


# --- Service layer (HTTP-agnostic) ---

class SignupFailure(Enum):
    USERNAME_TAKEN = "username_taken"
    WEAK_PASSWORD = "weak_password"

SignupResult: TypeAlias = User | SignupFailure


def signup_user(username: str, password: str) -> SignupResult:
    if User.objects.filter(username=username).exists():
        return SignupFailure.USERNAME_TAKEN
    if len(password) < 8:
        return SignupFailure.WEAK_PASSWORD
    return User.objects.create_user(username=username, password=password)


# --- HTTP responses ---
#
# Bind each failure variant to a semantic status base (Conflict, NotFound,
# BadRequest, Unauthorized, Forbidden, Gone, PayloadTooLarge,
# UnprocessableEntity, TooManyRequests, InternalServerError, MethodNotAllowed)
# parameterized on the enum member it represents. The wire `code` is derived
# from the member's `.value` — no string duplication.

class UsernameTaken(Conflict[SignupFailure.USERNAME_TAKEN]):
    message = "Username already exists"

class WeakPassword(BadRequest[SignupFailure.WEAK_PASSWORD]):
    message = "Password must be at least 8 characters"


# `Created[T]` (201), `Accepted[T]` (202), and `NoContent` (204) ship with
# hattori too — no need to declare your own success-status subclasses.


# --- Auth ---
#
# For one-off errors that don't correspond to a service-enum variant, use
# plain `ApiError` and set `code` and `error_code` directly.

class InvalidToken(ApiError):
    code = 401
    error_code = "invalid_token"
    message = "Invalid or missing token"


class BearerAuth(HttpBearer):
    def authenticate(self, request, token: str) -> User | InvalidToken:
        # `verify_token` is your own function — DB lookup, JWT verification,
        # whatever your app needs. Returning `InvalidToken()` short-circuits
        # to the 401 response; returning a User stores it on `request.auth`.
        user = verify_token(token)
        if user is None:
            return InvalidToken()
        return user


# --- Endpoints ---

@api.post("/signup")
def signup(
    request, data: SignupIn,
) -> Created[UserOut] | UsernameTaken | WeakPassword:
    match signup_user(data.username, data.password):
        case User() as user:
            return Created(UserOut(id=user.id, username=user.username))
        case SignupFailure.USERNAME_TAKEN:
            return UsernameTaken()
        case SignupFailure.WEAK_PASSWORD:
            return WeakPassword()


@api.get("/me", auth=BearerAuth())
def me(request) -> UserOut:
    user: User = request.auth
    return UserOut(id=user.id, username=user.username)
```

The service layer (`signup_user`) is HTTP-agnostic: it returns a `User` on success or a `SignupFailure` enum variant on any modeled failure. No status codes, no response bodies, no framework types — you could call it from a cron job or a management command and it'd just work.

The endpoint is where the translation happens. The `match` statement maps each service outcome to its HTTP response type. mypy sees the signatures on both sides, so adding a new `SignupFailure` variant without a matching arm fails type-checking before it hits runtime.

Wire it up in `urls.py`:

```python
from .api import api

urlpatterns = [
    path("api/", api.urls),
]
```

**That's it.** Every status code, request body, and response schema is auto-documented in your OpenAPI spec — no extra configuration needed.

### Return, don't raise

Signal any response — success or failure — by **returning** a typed value. The return annotation is the contract between your code and its clients: it drives runtime dispatch, the OpenAPI spec, and type-checking at the call site. Raising for control flow sidesteps all three.

```python
# good - explicit in the signature, type-checked, in the spec
def signup(request, data: SignupIn) -> UserRegistered | UsernameTaken:
    if taken: return UsernameTaken()
    ...

# avoid - not in the annotation, not in the spec, not type-checked
def signup(request, data: SignupIn) -> UserRegistered:
    if taken: raise HttpError(409, "Username taken")
    ...
```

This applies equally to endpoints and auth classes. Exceptions like `AuthenticationError` are framework-internal — hattori raises them when every auth callback returns `None` — not public API.

### What you get for free

- **Input validation** — `SignupIn` validates and type-casts the request body
- **Output filtering** — `UserOut` strips fields like `password` from the response
- **Multiple responses** — `UserRegistered | UsernameTaken` union types map directly to OpenAPI response schemas
- **Auth** — `401 Unauthorized` is auto-documented when `auth=` is set
- **422 errors** — validation error responses are added to the schema automatically
- **Interactive docs** — visit `/api/docs` for Swagger UI with everything above

## Multi-variant auth errors

The Quick Start's `BearerAuth` returns one failure type. For finer-grained errors — different reasons at different codes — model the failures as an enum and bind each variant to a typed response, the same way endpoints do:

```python
from enum import Enum
from hattori import Forbidden, Unauthorized
from hattori.security import HttpBearer


class TokenError(Enum):
    BAD_TOKEN = "bad_token"
    TOKEN_EXPIRED = "token_expired"
    ACCOUNT_LOCKED = "account_locked"


class BadToken(Unauthorized[TokenError.BAD_TOKEN]):
    message = "Token invalid or malformed"

class ExpiredToken(Unauthorized[TokenError.TOKEN_EXPIRED]):
    message = "Token has expired"

class AccountLocked(Forbidden[TokenError.ACCOUNT_LOCKED]):
    message = "Account is locked"


class BearerAuth(HttpBearer):
    def authenticate(
        self, request, token: str,
    ) -> User | BadToken | ExpiredToken | AccountLocked:
        if not parseable(token): return BadToken()
        if is_expired(token):    return ExpiredToken()
        if locked(user):         return AccountLocked()
        return user
```

Every operation using `auth=BearerAuth()` auto-documents `401` (with the union of `BadToken` + `ExpiredToken` bodies) and `403` (`AccountLocked`) in its OpenAPI response map — no per-endpoint wiring.

## Response types reference

Hattori ships typed response classes for the common status codes. Use these directly in return annotations — no need to declare your own subclasses for them.

### Success

| Class | Status | When |
|---|---|---|
| *(bare schema)* | 200 | Default — `-> UserOut` is implicitly 200 |
| `Created[T]` | 201 | `return Created(body)` |
| `Accepted[T]` | 202 | `return Accepted(body)` (queued / async work) |
| `NoContent` | 204 | `return NoContent()` (no body) |

### Errors (semantic `HTTPError` bases)

Subclass parameterized on a service enum member; the wire `code` is derived from `member.value`. The base class supplies the HTTP status, and OpenAPI emits a per-subclass error schema whose `code` field is `Literal[member.value]`. Multiple errors with the same HTTP status are emitted as a `oneOf` discriminated by `code`.

| Base | Status |
|---|---|
| `BadRequest[E]` | 400 |
| `Unauthorized[E]` | 401 |
| `Forbidden[E]` | 403 |
| `NotFound[E]` | 404 |
| `MethodNotAllowed[E]` | 405 |
| `Conflict[E]` | 409 |
| `Gone[E]` | 410 |
| `PayloadTooLarge[E]` | 413 |
| `UnprocessableEntity[E]` | 422 |
| `TooManyRequests[E]` | 429 |
| `InternalServerError[E]` | 500 |

```python
class DuplicateName(Conflict[CreateError.DUPLICATE_NAME]):
    message = "Already exists"
```

`Conflict[E.X]` and `Conflict[Literal[E.X]]` are interchangeable — pyright auto-promotes a bare enum member to a `Literal`.

### Escape hatches

For a one-off error that doesn't bind to a service-enum variant (typical for auth and infra), use plain `ApiError`:

```python
class InvalidToken(ApiError):
    code = 401
    error_code = "invalid_token"
    message = "Invalid or missing token"
```

For a different wire shape than `{code, message}`, subclass `APIReturn[YourBody]` directly:

```python
from hattori import APIReturn, Schema

class ProblemDetail(Schema):
    type: str
    title: str
    detail: str

class Gone(APIReturn[ProblemDetail]):
    code = 410
    def __init__(self, detail: str) -> None:
        super().__init__(ProblemDetail(
            type="https://example.com/probs/gone",
            title="Resource Gone",
            detail=detail,
        ))
```
