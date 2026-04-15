# Motivation

Django Hattori is an opinionated fork of [Django Ninja](https://github.com/vitalik/django-ninja), created by Vitaliy Kucheryaviy in 2020. Django Ninja solved a real problem: bringing FastAPI-style ergonomics and Pydantic validation to Django. This fork keeps that foundation and diverges on a few specific design calls — primarily around response typing, where the API is now built around explicit `APIReturn` subclasses instead of tuple-based status codes.

If you're deciding between the two, use Django Ninja if you want the stable, widely-adopted version. Use Django Hattori if you want the changes this fork has made (see [Releases](releases.md) for the concrete list).

## Why not just FastAPI?

!!! quote
    **Django Hattori** looks basically the same as **FastAPI**, so why not just use FastAPI?

The same answer the original project gave, which still holds:

1) **FastAPI** is ORM-agnostic, but if you use the Django ORM with FastAPI in sync mode, you can hit a [closed-connection issue](https://github.com/tiangolo/fastapi/issues/716) that takes a lot of effort to work around.

2) **Dependency injection with arguments** makes code verbose when nearly every endpoint depends on authentication and a database session:

```python hl_lines="25 26"
...

app = FastAPI()


# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_user(token: str = Depends(oauth2_scheme)):
    user = decode(token)
    if not user:
        raise HTTPException(...)
    return user


@app.get("/task/{task_id}", response_model=Task)
def read_user(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ... use db with current_user ....
```

3) The word `model` in Django is reserved for the ORM, so mixing Django ORM and Pydantic model naming gets confusing fast.

## What Django Hattori does differently

1) **Multiple API versions in one project.** You can run several `HattoriAPI` instances side-by-side:

```python
api_v1 = HattoriAPI(version="1.0", auth=token_auth)
api_v2 = HattoriAPI(version="2.0", auth=token_auth)
api_private = HattoriAPI(auth=session_auth, urls_namespace="private_api")


urlpatterns = [
    path("api/v1/", api_v1.urls),
    path("api/v2/", api_v2.urls),
    path("internal-api/", api_private.urls),
]
```

2) **Response typing is domain-first.** Every HTTP response is a named `APIReturn` subclass — `UserRegistered`, `UserNotFound`, `PaymentFailed` — and the status code lives on the class. A union of those in the return annotation is also the OpenAPI contract:

```python
@api.post("/signup")
def signup(request, data: SignupIn) -> UserRegistered | UsernameTaken:
    if taken: return UsernameTaken()
    return UserRegistered(UserOut(id=...))
```

3) **Strict schemas, explicit mapping.** Schemas validate dicts and explicit kwargs only — they don't coerce arbitrary ORM instances. You write `UserOut(id=user.id, username=user.username)` rather than `return user`. This costs a line of code and buys you: type-checked field mapping, refactoring safety when ORM fields change, and no hidden coupling between your API shape and your database schema.

4) **Authentication via `request` attributes**, not dependency injection — matches regular Django views. See [Authentication](guides/authentication.md) for details.
