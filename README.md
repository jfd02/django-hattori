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

from hattori import (
    ApiError, AuthedRequest, BadRequest, Conflict, Created, HattoriAPI, Schema,
)
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
# BadRequest, Unauthorized, PaymentRequired, Forbidden, Gone, PayloadTooLarge,
# UnprocessableEntity, TooManyRequests, InternalServerError, BadGateway,
# ServiceUnavailable, GatewayTimeout, MethodNotAllowed) parameterized on the
# enum member it represents. The wire `code` is derived from the member's
# `.value` — no string duplication.

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
def me(request: AuthedRequest[User]) -> UserOut:
    user = request.auth              # typed as User, no cast needed
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

## Typing `request.auth`

Successful authentication stashes its result on `request.auth`, but Django's `HttpRequest` doesn't declare that attribute — so annotating the parameter honestly makes every `request.auth` read a type error, and leaving it unannotated makes `request.auth` an untyped `Any`. `AuthedRequest[T]` is the annotation for it, parameterized on whatever your auth class returns:

```python
from hattori import AuthedRequest

@api.get("/me", auth=BearerAuth())
def me(request: AuthedRequest[User]) -> UserOut:
    return UserOut(id=request.auth.id, username=request.auth.username)
```

Permissions take the same annotation, since they run after authentication. Declare exactly the path params the check needs — the framework passes only those:

```python
class IsHouseholdAdmin(BasePermission):
    def check(self, request: AuthedRequest[User], household_id: str) -> bool:
        return is_admin(request.auth, household_id)
```

`AuthedRequest` is annotation-only: the object your view actually receives is Django's own `WSGIRequest`/`ASGIRequest`, so don't use it with `isinstance`. Only operations with `auth=` set populate `auth` — annotating an unauthenticated view with it claims an attribute that won't be there.

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
| `PaymentRequired[E]` | 402 |
| `Forbidden[E]` | 403 |
| `NotFound[E]` | 404 |
| `MethodNotAllowed[E]` | 405 |
| `Conflict[E]` | 409 |
| `Gone[E]` | 410 |
| `PayloadTooLarge[E]` | 413 |
| `UnprocessableEntity[E]` | 422 |
| `TooManyRequests[E]` | 429 |
| `InternalServerError[E]` | 500 |
| `BadGateway[E]` | 502 |
| `ServiceUnavailable[E]` | 503 |
| `GatewayTimeout[E]` | 504 |

```python
class DuplicateName(Conflict[CreateError.DUPLICATE_NAME]):
    message = "Already exists"
```

`Conflict[E.X]` and `Conflict[Literal[E.X]]` are interchangeable — pyright auto-promotes a bare enum member to a `Literal`.

For a status hattori doesn't ship a base for, declare your own with the exported `EnumT`:

```python
from hattori import EnumT, HTTPError

class NotImplementedYet(HTTPError[EnumT]):
    code: ClassVar[int] = 501
```

### Escape hatches

For a one-off error that doesn't bind to a service-enum variant (typical for auth and infra), use plain `ApiError`:

```python
class InvalidToken(ApiError):
    code = 401
    error_code = "invalid_token"
    message = "Invalid or missing token"
```

`ApiError` narrows `code` the same way the enum-keyed bases do: each subclass declaring an `error_code` gets its own OpenAPI schema with `code: Literal["invalid_token"]`, so generated clients can switch on it.

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

### Validation errors (422)

The 422 body is one model: its schema is what OpenAPI documents, and its `from_errors` is what the default handler sends. Swap it to change the shape without the spec and the response drifting apart:

```python
from hattori import ValidationErrorBody, set_validation_error_model

class Problem(ValidationErrorBody):
    code: Literal["validation_error"] = "validation_error"
    problems: list[FieldProblem]

    @classmethod
    def from_errors(cls, errors):
        return cls(problems=[FieldProblem(path=e["loc"], reason=e["msg"]) for e in errors])

set_validation_error_model(Problem)   # e.g. from AppConfig.ready()
```

The default is `{"detail": [{"loc": [...], "msg": ..., "type": ...}]}`. `loc` describes the request — `["body", "email"]`, `["query", "count"]` — never the handler's argument names.

### `HttpError` responses

`HttpError` is what the framework raises itself; a request body that can't be parsed is an `HttpError(400)`, so every operation with a body documents a 400. That body is one model too: its schema is what OpenAPI documents, and its `from_error` is what the default handler sends.

```python
from http import HTTPStatus
from hattori import HttpErrorBody, set_http_error_model

class Problem(HttpErrorBody):
    code: str
    message: str

    @classmethod
    def from_error(cls, error):
        return cls(code=HTTPStatus(error.status_code).name.lower(), message=str(error))

set_http_error_model(Problem)   # e.g. from AppConfig.ready()
```

The default is `{"detail": "<message>"}`.

Every error the API answers on your behalf is an `HttpError` too, so it takes the same body and the same `@api.exception_handler(HttpError)` override:

| Raised or requested | Answer |
| --- | --- |
| Django's `Http404` | 404 |
| Django's `PermissionDenied` | 403, as an `AuthorizationError` |
| Django's `BadRequest`, `SuspiciousOperation`, unreadable multipart data | 400 |
| A method the path doesn't declare | 405, with an `Allow` header |
| The API's root URL | 404 |

This holds wherever the exception is raised: the endpoint, its auth or permissions, or a view decorator around it. The Django exception is the `__cause__` of the `HttpError` your handler receives, and a handler registered for the Django exception itself takes precedence. Handlers run in a sync thread, so they can use the ORM even for an `async` endpoint.

A `GET` route also answers `HEAD`: the endpoint runs and the body is not sent. A streaming route is the exception. Every path answers `OPTIONS` with its `Allow` header, without running auth or the endpoint. Declare either operation yourself to replace that.

Two cases are left to Django: a path under the API that matches no route gets its 404 (shaped by `handler404`), and an exception no handler answers is re-raised outside `DEBUG`, so Django reports it. Register `@api.exception_handler(Exception)` to answer the latter yourself.

## Transactions

A returned error fails the request as fully as a raised one. With Django's
`ATOMIC_REQUESTS` on, any response of 400 or above that hattori produces —
returned or raised, from the endpoint, its auth or its permissions — rolls the
request's transaction back:

```python
@api.post("/signup")
def signup(request, data: SignupIn) -> Created[UserOut] | UsernameTaken:
    user = User.objects.create(username=data.username)
    if is_reserved(user):
        return UsernameTaken()       # the insert above is rolled back
    return Created(UserOut(id=user.id, username=user.username))
```

Only a hand-built `HttpResponse` is passed through on Django's own terms, which
commit unless an exception escapes.

For a write that has to outlive a failed request — a failed-login counter, an
audit row — opt the endpoint out with Django's own `non_atomic_requests` and
scope its transactions yourself:

```python
from django.db import transaction

@api.post("/login")
@transaction.non_atomic_requests
def login(request, data: LoginIn) -> SessionOut | BadCredentials:
    user = check_credentials(data.username, data.password)
    if user is None:
        FailedLogin.objects.create(username=data.username)   # kept
        return BadCredentials()
    return SessionOut(token=start_session(user))
```

Django applies the opt-out per URL, so it takes effect once every method
registered on that path carries it. It is also what lets an `async` endpoint run
in a project with `ATOMIC_REQUESTS` on, which Django otherwise refuses.

## Testing

Hattori ships a lightweight test client that calls your endpoints in-process —
no live server, no `urls.py` wiring. Point it at a `HattoriAPI` or a `Router`:

```python
from hattori.testing import TestClient

from .api import api

client = TestClient(api)


def test_signup():
    resp = client.post("/signup", json={"username": "neo", "password": "trinity!"})
    assert resp.status_code == 201
    assert resp.json() == {"id": 1, "username": "neo"}
```

Requests are built with Django's `RequestFactory`, so your handlers receive a
real `HttpRequest` — real `request.user` (an `AnonymousUser` by default), headers,
cookies, and body parsing — rather than a mock. Default headers and cookies can
be set once on the client:

```python
client = TestClient(api, headers={"Authorization": "Bearer t0ken"})
```

Per request, pass `json=`, form `data=`, `headers=`, `COOKIES=`, `FILES=`,
`query_params=`, or a custom authenticated `user=`:

```python
from django.core.files.uploadedfile import SimpleUploadedFile

client.get("/me", headers={"Authorization": "Bearer t0ken"})
client.post("/avatar", FILES={"file": SimpleUploadedFile("a.png", b"...")})
client.get("/dashboard", user=some_user)
```

Requests also include the resolved route in `request.resolver_match`, an async
`request.auser()` returning `request.user`, and a fresh dictionary for
`request.session`. Pass `session=` or `auser=` to override those defaults.

The response exposes `.status_code`, `.json()`, `.content`, and proxies header
access (`resp["Content-Type"]`).

With `ATOMIC_REQUESTS` on, each request runs in that transaction just as it does
behind Django's handler, so a failed request's writes are rolled back in tests
too.

### Async endpoints

Use `TestAsyncClient` and `await` the calls:

```python
from hattori.testing import TestAsyncClient

client = TestAsyncClient(api)


async def test_me():
    resp = await client.get("/me")
    assert resp.status_code == 200
```

### pytest fixtures

Opt in to the shipped fixtures by adding the plugin to your top-level
`conftest.py`:

```python
# conftest.py
pytest_plugins = ["hattori.testing.plugin"]
```

You then get `hattori_client` and `hattori_async_client` factory fixtures — call
them with the API or router under test (extra kwargs are forwarded to the
client):

```python
def test_signup(hattori_client):
    client = hattori_client(api)
    assert client.post("/signup", json={"username": "neo", "password": "x"}).status_code == 422
```

### Checking OpenAPI changes

For package development, run `make test-openapi` to validate exported documents
with `openapi-spec-validator` and check representative runtime responses against
their JSON Schemas. These tests also reject duplicate JSON keys, unresolved local
references, and discriminator mappings that disagree with their union variants.
They run as part of the regular test suite in CI.

When adding a schema feature, add a case in `tests/test_openapi_contract.py` using
`export_contract(api)` and `validate_response(document, path, response)` from
`tests.openapi_contract`. Include both valid and invalid requests when input
validation is involved. Snapshots still document intentional output changes;
contract checks verify that the output is valid and matches runtime behavior.

The same command runs Hypothesis property tests that generate model graphs,
colliding component names, route orders, nullable query schemas, aliases,
serialization options, and error unions. Each generated API must export a valid
contract and its responses must satisfy that contract. The normal profile tries
40 examples per property; `make test-openapi-deep` raises that to 500 and prints
search statistics for both the model properties and HTTP tests.

`make test-openapi-http` runs Schemathesis against a stateless fixture API on
pytest-django's temporary local HTTP server, including middleware and URL routing.
It reads the served OpenAPI document and generates valid and invalid requests for JSON bodies,
discriminated unions, path and query parameters, CSV and repeated arrays, forms,
uploads, typed errors, and async views. Checks cover server errors, documented
statuses, content types, response schemas, valid-input acceptance, and invalid-input
rejection. This suite is also included in `make test-openapi` and normal CI runs;
it starts and stops its own server and needs no external database. Django's request
lifecycle uses the test database, but the fixture endpoints are stateless.
The normal fuzzing budget is 40 examples per operation; schema examples and
boundary cases run in addition.

The fixture's strict JSON models reject coercions, with integral floats accepted
to match JSON Schema's integer semantics. Multipart invalid-input rejection is
checked explicitly for missing files: generated non-string fields can become
valid strings during multipart encoding, so that one automatic check is disabled
for the upload endpoint. Its response and valid-input checks remain enabled.
Add representative routes to `tests/schemathesis_app.py` as features grow.

The integer path fixture also accepts Pydantic's numeric text forms such as
`+0.0`. When that path is the only component generated as invalid and Pydantic
can parse it, the HTTP test skips the invalid-input rejection check for that
case. Response checks still run, and explicit cases verify that nonnumeric and
fractional path values return 422.

Hypothesis shrinks a failure to a small counterexample and saves it locally in
`.hypothesis/` for replay. Keep newly discovered bugs as explicit regression
tests after fixing them. To repeat a generated search, pass a fixed seed, for
example `uv run pytest tests/test_openapi_properties.py --hypothesis-seed=1234`.

Typed responses use Pydantic's JSON serialization when using `JSONRenderer` or
streaming JSON, including serializers with `when_used="json"`. Custom renderers
receive Python values by default; set `serialization_mode = "json"` on a custom
renderer if it needs JSON-compatible values instead.
