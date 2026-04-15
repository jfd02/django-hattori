# Response Schema

**Django Hattori** allows you to define the schema of your responses both for validation and documentation purposes.

Imagine you need to create an API operation that creates a user. The **input** parameter would be **username+password**, but **output** of this operation should be **id+username** (**without** the password).

Let's create the input schema:

```python hl_lines="3 5"
from hattori import Schema

class UserIn(Schema):
    username: str
    password: str


@api.post("/users/")
def create_user(request, data: UserIn):
    user = User(username=data.username) # User is django auth.User
    user.set_password(data.password)
    user.save()
    # ... return ?
```

Now declare the output schema as the function's return type:

```python hl_lines="1 9 14"
from hattori import Schema

class UserIn(Schema):
    username: str
    password: str


class UserOut(Schema):
    id: int
    username: str


@api.post("/users/")
def create_user(request, data: UserIn) -> UserOut:
    user = User(username=data.username)
    user.set_password(data.password)
    user.save()
    return user
```

**Django Hattori** will use this response schema to:

- convert the output data to declared schema
- validate the data
- add an OpenAPI schema definition
- it will be used by the automatic documentation systems
- and, most importantly, it **will limit the output data** only to the fields only defined in the schema.

A bare return type (here `UserOut`) implicitly maps to HTTP **200**. You don't need to wrap the return value — just return the object.

## Nested objects

There is also often a need to return responses with some nested/child objects.

Imagine we have a `Task` Django model with a `User` ForeignKey:

```python hl_lines="6"
from django.db import models

class Task(models.Model):
    title = models.CharField(max_length=200)
    is_completed = models.BooleanField(default=False)
    owner = models.ForeignKey("auth.User", null=True, blank=True)
```

Now let's output all tasks, and for each task, output some fields about the user.

```python hl_lines="1 12 16"
from hattori import Schema

class UserSchema(Schema):
    id: int
    first_name: str
    last_name: str

class TaskSchema(Schema):
    id: int
    title: str
    is_completed: bool
    owner: UserSchema = None  # ! None - to mark it as optional


@api.get("/tasks")
def tasks(request) -> list[TaskSchema]:
    queryset = Task.objects.select_related("owner")
    return list(queryset)
```

If you execute this operation, you should get a response like this:

```JSON hl_lines="6 7 8 9 16"
[
    {
        "id": 1,
        "title": "Task 1",
        "is_completed": false,
        "owner": {
            "id": 1,
            "first_name": "John",
            "last_name": "Doe",
        }
    },
    {
        "id": 2,
        "title": "Task 2",
        "is_completed": false,
        "owner": null
    },
]
```

## Aliases

Instead of a nested response, you may want to just flatten the response output.
The Hattori `Schema` object extends Pydantic's `Field(..., alias="")` format to
work with dotted responses.

Using the models from above, let's make a schema that just includes the task
owner's first name inline, and also uses `completed` rather than `is_completed`:

```python hl_lines="1 7-9"
from hattori import Field, Schema


class TaskSchema(Schema):
    id: int
    title: str
    # The first Field param is the default, use ... for required fields.
    completed: bool = Field(..., alias="is_completed")
    owner_first_name: str = Field(None, alias="owner.first_name")
```

Aliases also support django template syntax variables access:

```python hl_lines="2"
class TaskSchema(Schema):
    last_message: str = Field(None, alias="message_set.0.text")
```

```python hl_lines="3"
class TaskSchema(Schema):
    type: str = Field(None)
    type_display: str = Field(None, alias="get_type_display") # callable will be executed
```

## Resolvers

You can also create calculated fields via resolve methods based on the field
name.

The method must accept a single argument, which will be the object the schema
is resolving against.

When creating a resolver as a standard method, `self` gives you access to other
validated and formatted attributes in the schema.

```python hl_lines="5 7-11"
class TaskSchema(Schema):
    id: int
    title: str
    is_completed: bool
    owner: Optional[str] = None
    lower_title: str

    @staticmethod
    def resolve_owner(obj):
        if not obj.owner:
            return
        return f"{obj.owner.first_name} {obj.owner.last_name}"

    def resolve_lower_title(self, obj):
        return self.title.lower()
```

### Accessing extra context

Pydantic v2 allows you to process an extra context that is passed to the serializer. In the following example you can have resolver that gets request object from passed `context` argument:

```python hl_lines="6"
class Data(Schema):
    a: int
    path: str = ""

    @staticmethod
    def resolve_path(obj, context):
        request = context["request"]
        return request.path
```

if you use this schema for incoming requests - the `request` object will be automatically passed to context.

You can as well pass your own context:

```python
data = Data.model_validate({'some': 1}, context={'request': MyRequest()})
```

## Returning querysets

In the previous example we specifically converted a queryset into a list (and executed the SQL query during evaluation).

You can avoid that and return a queryset as a result, and it will be automatically evaluated to List:

```python hl_lines="3"
@api.get("/tasks")
def tasks(request) -> list[TaskSchema]:
    return Task.objects.all()
```

!!! warning

    If your operation is async, this example will not work because the ORM query needs to be called safely.

    ```python hl_lines="2"
    @api.get("/tasks")
    async def tasks(request) -> list[TaskSchema]:
        return Task.objects.all()
    ```

    See the [async support](../async-support.md#using-orm) guide for more information.

## FileField and ImageField

**Django Hattori** by default converts files and images (declared with `FileField` or `ImageField`) to `string` URL's.

An example:

```python hl_lines="3"
class Picture(models.Model):
    title = models.CharField(max_length=100)
    image = models.ImageField(upload_to='images')
```

If you need to output to response image field, declare a schema for it as follows:

```python hl_lines="3"
class PictureSchema(Schema):
    title: str
    image: str
```

Once you output this to a response, the URL will be automatically generated for each object:

```JSON
{
    "title": "Zebra",
    "image": "/static/images/zebra.jpg"
}
```

## Multiple Response Schemas

For non-200 responses, subclass `APIReturn` to pin a status code to a body type:

```python
from hattori import APIReturn, Schema

class Token(Schema):
    token: str

class Message(Schema):
    message: str

class Unauthorized(APIReturn[Message]):
    code = 401

@api.post("/login")
def login(request, payload: Auth) -> Token | Unauthorized:
    if auth_not_valid:
        return Unauthorized(Message(message="bad credentials"))
    return Token(token=xxx)
```

The bare `Token` is the implicit 200 schema. Each `APIReturn` subclass contributes its `code` to the OpenAPI spec.

### Errors with `ApiError`

For the common `{code, message}` error shape, hattori ships `ApiError` so you skip the boilerplate entirely:

```python
from hattori import ApiError

class UserNotFound(ApiError):
    code = 404
    error_code = "user_not_found"
    message = "No user with that id"     # static default

@api.get("/users/{id}")
def get_user(request, id: int) -> UserOut | UserNotFound:
    if not found: return UserNotFound()              # uses static message
    return user

# Override at the call site when the message is dynamic:
return UserNotFound(f"No user with id {id}")
```

For a different error body shape (RFC 7807 problem details, nested field errors, etc.), skip `ApiError` and subclass `APIReturn[YourBody]` directly — define your own `__init__` to shape the payload.

## Empty responses

Some responses, such as [204 No Content](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/204), have no body. Declare a subclass with `None` as the payload type:

```python hl_lines="1 3-4 7"
from hattori import APIReturn

class NoContent(APIReturn[None]):
    code = 204

@api.post("/no_content")
def no_content(request) -> NoContent:
    return NoContent(None)
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
    return HttpResponse('some data')   # !!!!


@api.get("/something")
def some_redirect(request):
    return redirect("/some-path")  # !!!!
```
