# Response Schema

Hattori schemas are **strict data types**. They validate dicts and accept explicit kwargs; they do **not** coerce Django ORM instances or arbitrary attribute-bearing objects. If you want to return a schema from a view, construct it explicitly.

Imagine an endpoint that creates a user. The **input** is `username + password`, the **output** is `id + username` (without the password):

```python hl_lines="3 9 14 18"
from hattori import Schema

class UserIn(Schema):
    username: str
    password: str

class UserOut(Schema):
    id: int
    username: str


@api.post("/users/")
def create_user(request, data: UserIn) -> UserOut:
    user = User(username=data.username)  # Django auth.User
    user.set_password(data.password)
    user.save()
    return UserOut(id=user.id, username=user.username)
```

The return type `-> UserOut` implicitly maps to HTTP **200**. Hattori validates the `UserOut(...)` instance against the schema, serializes it to JSON, and emits the shape into the OpenAPI spec. No extra decorators, no response-model parameter.

## Why explicit construction?

Schemas don't pull fields off arbitrary objects. That means:

- **Renaming a Django field won't silently break the API** — mypy/pyright see it immediately.
- **The ORM → response mapping lives in your code**, where you can grep for it, test it, and refactor it.
- **No hidden field aliasing, no callable resolution, no queryset magic.** A schema is a dataclass.

For repeated mappings, centralize them:

```python
def user_to_schema(user: User) -> UserOut:
    return UserOut(id=user.id, username=user.username)

@api.get("/users/{id}")
def get_user(request, id: int) -> UserOut:
    return user_to_schema(User.objects.get(id=id))
```

## Lists and collections

Return a list of schemas the same way — construct each explicitly:

```python
@api.get("/users")
def list_users(request) -> list[UserOut]:
    return [UserOut(id=u.id, username=u.username) for u in User.objects.all()]
```

## Nested schemas

Schemas compose — a field can be another schema, and you construct the nested one the same way:

```python
class TaskOwner(Schema):
    id: int
    first_name: str

class TaskOut(Schema):
    id: int
    title: str
    owner: TaskOwner | None = None


@api.get("/tasks")
def tasks(request) -> list[TaskOut]:
    return [
        TaskOut(
            id=t.id,
            title=t.title,
            owner=TaskOwner(id=t.owner.id, first_name=t.owner.first_name)
            if t.owner
            else None,
        )
        for t in Task.objects.select_related("owner").all()
    ]
```

Response:

```JSON
[
  {"id": 1, "title": "Task 1", "owner": {"id": 1, "first_name": "John"}},
  {"id": 2, "title": "Task 2", "owner": null}
]
```

## Multiple Response Schemas

A bare schema in the return annotation is the implicit 200. For other status codes, hattori ships typed primitives — you don't need to declare your own subclasses for common cases.

### Success status codes

`Created[T]` (201), `Accepted[T]` (202), and `NoContent` (204) cover the bulk of 2xx returns:

```python
from hattori import Accepted, Created, NoContent

@api.post("/users")
def create_user(request, data: UserIn) -> Created[UserOut]:
    user = User.objects.create(...)
    return Created(UserOut(id=user.id, username=user.username))

@api.post("/jobs")
def queue_job(request, data: JobIn) -> Accepted[JobOut]:
    job_id = enqueue(...)
    return Accepted(JobOut(job_id=job_id))

@api.delete("/users/{id}")
def delete_user(request, id: int) -> NoContent:
    User.objects.filter(id=id).delete()
    return NoContent()
```

The status code is encoded in the type — the OpenAPI spec, the runtime status, and the type checker all read it from the same place.

### Errors keyed by an enum

For services that model expected failures as an `Enum`, bind each variant to a semantic status base (`Conflict`, `NotFound`, `BadRequest`, `Unauthorized`, `Forbidden`, `MethodNotAllowed`, `Gone`, `PayloadTooLarge`, `UnprocessableEntity`, `TooManyRequests`, `InternalServerError`). Parameterize on the enum member; the wire `code` field is derived from `member.value` automatically.

```python
from enum import Enum
from hattori import Conflict, NotFound

class CreateUserError(Enum):
    USERNAME_TAKEN = "username_taken"
    GROUP_NOT_FOUND = "group_not_found"


class UsernameTaken(Conflict[CreateUserError.USERNAME_TAKEN]):
    message = "Username already exists"

class GroupNotFound(NotFound[CreateUserError.GROUP_NOT_FOUND]):
    message = "Group not found"


@api.post("/users")
def create_user(request, data: UserIn) -> Created[UserOut] | UsernameTaken | GroupNotFound:
    match create_user_service(data):
        case User() as user:
            return Created(UserOut(id=user.id, username=user.username))
        case CreateUserError.USERNAME_TAKEN:
            return UsernameTaken()
        case CreateUserError.GROUP_NOT_FOUND:
            return GroupNotFound()
```

The class hierarchy encodes the HTTP semantics (`Conflict` → 409); the type parameter encodes the wire `code` (`"username_taken"`); the `message` ClassVar is the default body. Override per-call when needed: `return UsernameTaken("dynamic msg")`.

### One-off errors with `ApiError`

For errors that don't correspond to a service-enum variant — typically auth and infrastructure errors raised at points where there's nothing to bind to — use plain `ApiError`:

```python
from hattori import ApiError

class InvalidToken(ApiError):
    code = 401
    error_code = "invalid_token"
    message = "Invalid or missing token"
```

### Custom error body shapes

If you need a different wire shape than `{code, message}`, subclass `APIReturn[YourBody]` directly and define `__init__` to shape the payload:

```python
from hattori import APIReturn, Schema

class ProblemDetail(Schema):
    type: str
    title: str
    detail: str

class GoneProblem(APIReturn[ProblemDetail]):
    code = 410
    def __init__(self, detail: str) -> None:
        super().__init__(ProblemDetail(
            type="https://example.com/probs/gone",
            title="Resource Gone",
            detail=detail,
        ))
```

## Error responses

Check [Handling errors](../errors.md) for more information.

## Django HTTP responses

It is also possible to return regular django http responses:

```python
from django.http import HttpResponse
from django.shortcuts import redirect


@api.get("/http")
def result_django(request):
    return HttpResponse("some data")


@api.get("/something")
def some_redirect(request):
    return redirect("/some-path")
```
