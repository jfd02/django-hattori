# Tutorial - Handling Responses

## Define a response Schema

**Django Hattori** allows you to define the schema of your responses both for validation and documentation purposes.

We'll create a third operation that will return information about the current Django user.

```python
from hattori import Schema

class UserSchema(Schema):
    username: str
    is_authenticated: bool
    # Unauthenticated users don't have the following fields, so provide defaults.
    email: str = None
    first_name: str = None
    last_name: str = None

@api.get("/me")
def me(request) -> UserSchema:
    return request.user
```

This will convert the Django `User` object into a dictionary of only the defined fields.

### Multiple response types

Return a different response if the user is not authenticated. Hattori ships `ApiError` for `{code, message}` error bodies — subclass it to bind a status code:

```python hl_lines="1 9-12 16"
from hattori import ApiError, Schema

class UserSchema(Schema):
    username: str
    email: str
    first_name: str
    last_name: str

class NotSignedIn(ApiError):
    code = 403
    error_code = "not_signed_in"
    message = "Please sign in first"

@api.get("/me")
def me(request) -> UserSchema | NotSignedIn:
    if not request.user.is_authenticated:
        return NotSignedIn()
    return request.user
```

A bare return type (`UserSchema`) is the implicit 200. Each extra response is a named class whose `code` class attribute pins its HTTP status.

For richer patterns — services that model multiple failure variants as an `Enum`, the `Created[T]` / `NoContent` success helpers, and the semantic status bases (`Conflict`, `NotFound`, `BadRequest`, …) that derive the wire `code` from an enum member — see [Response Schema](../guides/response/index.md).

!!! success

    That concludes the tutorial! Check out the **Other Tutorials** or the **How-to Guides** for more information.
