# Handling errors

**Django Hattori** allows you to install custom exception handlers to deal with how you return responses when errors or handled exceptions occur.

## Custom exception handlers

Let's say you are making API that depends on some external service that is designed to be unavailable at some moments. Instead of throwing default 500 error upon exception - you can handle the error and give some friendly response back to the client (to come back later)

To achieve that you need:

1. create some exception (or use existing one)
2. use api.exception_handler decorator


Example:


```python hl_lines="9 10"
api = HattoriAPI()

class ServiceUnavailableError(Exception):
    pass


# initializing handler

@api.exception_handler(ServiceUnavailableError)
def service_unavailable(request, exc):
    return api.create_response(
        request,
        {"message": "Please retry later"},
        status=503,
    )


# some logic that throws exception

@api.get("/service")
def some_operation(request):
    if random.choice([True, False]):
        raise ServiceUnavailableError()
    return {"message": "Hello"}

```

Exception handler function takes 2 arguments:

 - **request** - Django http request
 - **exc** - actual exception

function must return http response

## Override the default exception handlers

**Django Hattori** registers default exception handlers for the types shown below.
You can register your own handlers with `@api.exception_handler` to override the default handlers.

#### `hattori.errors.AuthenticationError`

Raised when authentication data is not valid

#### `hattori.errors.AuthorizationError`

Raised when authentication data is valid, but doesn't allow you to access the resource

#### `hattori.errors.ValidationError`

Raised when request data does not validate

#### `hattori.errors.HttpError`

Used to throw http error with status code from any place of the code

#### `django.http.Http404`
 
 Django's default 404 exception (can be returned f.e. with `get_object_or_404`)

#### `Exception`
 
Any other unhandled exception by application.

Default behavior 
 
  - **if `settings.DEBUG` is `True`** - returns a traceback in plain text (useful when debugging in console or swagger UI)
  - **else** - default django exception handler mechanism is used (error logging, email to ADMINS)


## Customizing request validation errors

Requests that fail validation raise `hattori.errors.ValidationError` (not to be confused with `pydantic.ValidationError`).
`ValidationError`s have a default exception handler that returns a 422 (Unprocessable Content) JSON response of the form:
```json
{
    "detail": [ ... ]
}
```

You can change this behavior by overriding the default handler for `ValidationError`s:

```python hl_lines="1 4"
from hattori.errors import ValidationError
...

@api.exception_handler(ValidationError)
def validation_errors(request, exc):
    return HttpResponse("Invalid input", status=422)
```

If you need even more control over validation errors (for example, if you need to reference the schema associated with
the model that failed validation), you can supply your own `validation_error_from_error_contexts` in a `HattoriAPI` subclass:

```python hl_lines="4"
from hattori.errors import ValidationError, ValidationErrorContext
from typing import Any, Dict, List

class CustomHattoriAPI(HattoriAPI):
    def validation_error_from_error_contexts(
        self, error_contexts: List[ValidationErrorContext],
    ) -> ValidationError:
        custom_error_infos: List[Dict[str, Any]] = []
        for context in error_contexts:
            model = context.model
            pydantic_schema = model.__pydantic_core_schema__
            param_source = model.__hattori_param_source__
            for e in context.pydantic_validation_error.errors(
                include_url=False, include_context=False, include_input=False
            ):
                custom_error_info = {
                # TODO: use `e`, `param_source`, and `pydantic_schema` as desired
                }
                custom_error_infos.append(custom_error_info)
        return ValidationError(custom_error_infos)

api = CustomHattoriAPI()
```

Now each `ValidationError` raised during request validation will contain data from your `validation_error_from_error_contexts`.

## 422 responses in the OpenAPI schema

Django Hattori automatically adds a `422 Unprocessable Content` response to the OpenAPI schema for any operation that has validatable parameters (query, path, body, form, etc.). This documents the validation error response shape that clients can expect when input fails pydantic validation.

Operations with no parameters will not have a 422 response in the schema.

If you need to customize the 422 response schema (for example, because you changed the error format with a custom exception handler), you can override it by explicitly declaring a 422 response:

```python
from hattori import APIReturn

class CustomError(Schema):
    message: str
    errors: list

class ValidationFailed(APIReturn[CustomError]):
    code = 422

@api.post("/items")
def create_item(request, data: ItemIn) -> Item | ValidationFailed:
    ...
```

When you explicitly include a `422` response in your return type annotation, Django Hattori will use your schema instead of the default one.


## Throwing HTTP responses with exceptions

As an alternative to custom exceptions and writing handlers for it - you can as well throw http exception that will lead to returning a http response with desired code


```python
from hattori.errors import HttpError

@api.get("/some/resource")
def some_operation(request):
    if True:
        raise HttpError(503, "Service Unavailable. Please retry later.")

```
