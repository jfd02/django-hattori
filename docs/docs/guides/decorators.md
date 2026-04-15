# Decorators

Hattori lets you wrap API operations with cross-cutting logic — logging, timing, caching, rate limiting, feature flags. There are two ways to apply a decorator to many operations at once (`add_decorator` on an API or router), plus a single-endpoint escape hatch (`@decorate_view`) for Django's built-in view decorators.

## Two modes

### OPERATION mode (default)

- Runs **after** request validation.
- Receives the parsed, validated parameters and the view's return value (a typed `APIReturn` instance or schema).
- Right for: logging with validated data, business-logic side effects, metrics, audit trails.

### VIEW mode

- Runs **before** request validation.
- Wraps the raw Django view, receives the raw `HttpRequest`, returns an `HttpResponse`.
- Right for: caching at the HTTP level, response headers, rate limiting — anything that works with the request/response objects rather than the parsed view payload.
- Equivalent to Django's standard view decorators.

## `@decorate_view`

Apply a Django view decorator to a single endpoint:

```python
from django.views.decorators.cache import cache_page
from hattori import HattoriAPI, Schema
from hattori.decorators import decorate_view

api = HattoriAPI()


class CachedPayload(Schema):
    data: str


@api.get("/cached")
@decorate_view(cache_page(60 * 15))  # 15 minutes
def cached_endpoint(request) -> CachedPayload:
    return CachedPayload(data="cached for 15 minutes")
```

Stack multiple decorators:

```python
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers

@api.get("/multi")
@decorate_view(cache_page(300), vary_on_headers("User-Agent"))
def multi_decorated(request) -> CachedPayload:
    return CachedPayload(data="cached, varied by UA")
```

## `add_decorator`

Apply a decorator to every operation on a router or API.

### Router-level

```python
import logging
from functools import wraps

from hattori import Router, Schema

router = Router()
log = logging.getLogger(__name__)


def log_operation(func):
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        log.info("calling %s path=%s", func.__name__, request.path)
        result = func(request, *args, **kwargs)
        log.info("returned %s from %s", type(result).__name__, func.__name__)
        return result
    return wrapper


router.add_decorator(log_operation)  # OPERATION mode by default


class Users(Schema):
    users: list[str]


@router.get("/users")
def list_users(request) -> Users:
    return Users(users=["Alice", "Bob"])
```

Operation-mode decorators see the typed return value, so you can branch on response type:

```python
from hattori import APIReturn

def audit(func):
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        result = func(request, *args, **kwargs)
        if isinstance(result, APIReturn):
            log.info("%s emitted %s (code=%d)",
                     func.__name__, type(result).__name__, type(result).code)
        return result
    return wrapper
```

### API-level (VIEW mode)

VIEW-mode decorators see the raw request and response — perfect for response headers:

```python
def cors_headers(func):
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        response = func(request, *args, **kwargs)
        response["Access-Control-Allow-Origin"] = "*"
        return response
    return wrapper


api = HattoriAPI()
api.add_decorator(cors_headers, mode="view")
```

Every endpoint now emits the CORS header. No per-endpoint wiring.

## Timing via response header (VIEW mode)

Timing the view is a classic case where VIEW mode is the right fit — you want to mutate the HTTP response, not the payload:

```python
import time
from functools import wraps


def timing_header(func):
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        start = time.monotonic()
        response = func(request, *args, **kwargs)
        response["X-Response-Time-ms"] = f"{(time.monotonic() - start) * 1000:.1f}"
        return response
    return wrapper


api.add_decorator(timing_header, mode="view")
```

The endpoint's typed return remains untouched; the timing lives on the HTTP response as a header.

## Response caching (VIEW mode)

```python
import hashlib
from functools import wraps

from django.core.cache import cache


def cache_response(timeout=300):
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            key = hashlib.md5(
                f"{request.path}{request.GET.urlencode()}".encode()
            ).hexdigest()
            cached = cache.get(key)
            if cached:
                return cached
            response = func(request, *args, **kwargs)
            cache.set(key, response, timeout)
            return response
        return wrapper
    return decorator


router.add_decorator(cache_response(600), mode="view")
```

## Execution order

Within one mode, decorators cascade outermost → innermost:

1. API-level decorators
2. Parent-router decorators
3. Child-router decorators
4. Endpoint decorators (`@decorate_view`)

```python
api.add_decorator(api_decorator)
parent_router.add_decorator(parent_decorator)
child_router.add_decorator(child_decorator)

@child_router.get("/test")
def endpoint(request) -> ...:
    ...

# Order: api_decorator → parent_decorator → child_decorator → endpoint
```

!!! note "VIEW mode always runs before OPERATION mode"
    When both modes are stacked on an endpoint, every VIEW decorator executes before any OPERATION decorator, regardless of cascade position. Compare ordering within a single mode only.

## Async

### Async-only router

If every endpoint on a router is async, write an async decorator directly:

```python
import asyncio
from functools import wraps


def async_timing_header(func):
    @wraps(func)
    async def wrapper(request, *args, **kwargs):
        start = time.monotonic()
        response = await func(request, *args, **kwargs)
        response["X-Response-Time-ms"] = f"{(time.monotonic() - start) * 1000:.1f}"
        return response
    return wrapper


router.add_decorator(async_timing_header, mode="view")
```

### Mixed sync + async router

If a router hosts both, detect and branch:

```python
import asyncio


def universal_log(func):
    if asyncio.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(request, *args, **kwargs):
            log.info("%s (async)", func.__name__)
            return await func(request, *args, **kwargs)
        return async_wrapper
    else:
        @wraps(func)
        def sync_wrapper(request, *args, **kwargs):
            log.info("%s (sync)", func.__name__)
            return func(request, *args, **kwargs)
        return sync_wrapper


router.add_decorator(universal_log)
```

## When to use each mode

| Use OPERATION mode when... | Use VIEW mode when... |
|---|---|
| You care about the typed return value (logging, audit, metrics) | You need the raw `HttpResponse` object (headers, caching) |
| You want to branch on `APIReturn` subclass | You want to short-circuit before validation (auth, rate limiting) |
| Side effects tied to business outcomes | You're wrapping behavior that's conceptually HTTP-level |

## Best practices

1. **Always `@wraps(func)`** so hattori can still read the original function's annotations and signature.
2. **Don't mutate the return value.** Schemas are strict — adding fields like `_timing` breaks validation. Use headers or logs instead.
3. **Prefer VIEW mode for HTTP-level concerns** (headers, caching, rate limiting). Prefer OPERATION mode for domain-level concerns (logging outcomes, metrics, audit).
4. **Keep decorators focused.** One concern per decorator.
5. **Test both sync and async paths** if your router is mixed.
