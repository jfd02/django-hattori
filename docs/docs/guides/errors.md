# Handling errors

Hattori's first answer to errors is **return a typed `APIReturn` subclass** — the return annotation is the contract, and every expected failure belongs there. See [Response Schema](response/index.md) and the [Quick Start](../index.md) for the pattern.

This guide covers the other case: **exceptions you don't control**. A third-party client times out, a DB driver raises, a Django helper throws `Http404`. Exception handlers are where you translate those into HTTP responses without polluting every view's return type with things that can happen anywhere.

## Catching third-party exceptions

Say a payment processor SDK raises `PaymentProviderTimeout` somewhere deep in your code. You don't want every endpoint that transitively calls it to list that exception in its return annotation. Register a handler once:

```python hl_lines="10"
from payments.sdk import PaymentProviderTimeout

api = HattoriAPI()


@api.exception_handler(PaymentProviderTimeout)
def handle_payment_timeout(request, exc):
    return api.create_response(
        request,
        {"error": "payment_provider_unavailable", "retry_after": 30},
        status=503,
    )
```

Any request that eventually raises `PaymentProviderTimeout` now returns a 503 with that body. The handler is the translation layer for exceptions the same way an endpoint's `match` is the translation layer for return values.

!!! warning
    **This is not for your own control flow.** Code you wrote should return typed `APIReturn` subclasses, not raise. Exception handlers exist for *external* exceptions you can't restructure around. If you're considering raising a custom `ServiceUnavailable` exception inside your own service layer, model it as a service-result variant instead and match on it in the endpoint.

Handler signature: `(request, exc) -> HttpResponse`.

## Default exception handlers

Hattori registers handlers for these automatically. You can replace any of them with `@api.exception_handler`:

| Exception | When it's raised | Default response |
|---|---|---|
| `django.http.Http404` | e.g. by `get_object_or_404` | 404 `{"detail": "Not Found"}` |
| `hattori.errors.ValidationError` | request parameters fail validation | 422 with `detail: [...]` |
| `hattori.errors.HttpError` | framework-internal (see below) | status code + `{"detail": message}` |
| `hattori.errors.AuthenticationError` | framework-internal: all auth callbacks returned `None` | 401 `{"detail": "Unauthorized"}` |
| `hattori.errors.AuthorizationError` | framework-internal | 403 `{"detail": "Forbidden"}` |
| `Exception` | anything else unhandled | traceback in `DEBUG`, Django's default handler otherwise |

`HttpError`, `AuthenticationError`, `AuthorizationError` are framework-internal — hattori raises them itself, and the handlers exist so you can customize the response shape if you don't like the default. Your own code shouldn't raise them.

## Customizing request validation errors

Requests that fail validation raise `hattori.errors.ValidationError` (not `pydantic.ValidationError`). The default handler returns a 422 response of the form `{"detail": [...]}`.

Override with a custom handler:

```python hl_lines="1 4"
from hattori.errors import ValidationError

@api.exception_handler(ValidationError)
def validation_errors(request, exc):
    return HttpResponse("Invalid input", status=422)
```

If you need richer control — e.g. referencing the schema associated with the failed model — subclass `HattoriAPI` and override `validation_error_from_error_contexts`:

```python hl_lines="4"
from hattori.errors import ValidationError, ValidationErrorContext

class CustomHattoriAPI(HattoriAPI):
    def validation_error_from_error_contexts(
        self, error_contexts: list[ValidationErrorContext],
    ) -> ValidationError:
        custom_error_infos: list[dict] = []
        for context in error_contexts:
            model = context.model
            pydantic_schema = model.__pydantic_core_schema__
            param_source = model.__hattori_param_source__
            for e in context.pydantic_validation_error.errors(
                include_url=False, include_context=False, include_input=False
            ):
                # shape `e`, `param_source`, `pydantic_schema` however you want
                custom_error_infos.append({...})
        return ValidationError(custom_error_infos)

api = CustomHattoriAPI()
```

## 422 responses in the OpenAPI schema

Hattori automatically adds a `422 Unprocessable Content` response to the OpenAPI schema for any operation that has validatable parameters (query, path, body, form, etc.). Operations with no parameters don't get one.

If you customized the validation error format, override the 422 schema by declaring it explicitly in the return annotation:

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

The explicit declaration wins over the default.
