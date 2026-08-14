"""A 422 is documented only when some input could actually produce one.

Params are not uniformly fallible. Path/query/header/cookie values arrive as
strings, so an unconstrained ``str`` that can't be absent has nothing to
coerce and no constraint to violate — advertising a 422 there promises a
response the route can never send. Anything narrower (a coerced type, a
constraint, a validator, a required param that can be omitted, a body) can
fail, and must keep its 422.
"""

from typing import Annotated

from pydantic import AfterValidator, Field

from hattori import HattoriAPI, Header, Query, Schema
from hattori.testing import TestClient


def _reject_empty(value: str) -> str:
    if not value:
        raise ValueError("must not be empty")
    return value


Validated = Annotated[str, AfterValidator(_reject_empty)]

api = HattoriAPI()


class Out(Schema):
    ok: str


class Payload(Schema):
    x: int


# --- cannot produce a 422 -------------------------------------------------


@api.get("/no-params")
def no_params(request) -> Out:
    return Out(ok="")


@api.get("/str-path/{hid}")
def str_path(request, hid: str) -> Out:
    return Out(ok=hid)


@api.get("/str-path-two/{hid}/{oid}")
def str_path_two(request, hid: str, oid: str) -> Out:
    return Out(ok=hid + oid)


@api.get("/optional-str-query")
def optional_str_query(request, token: str | None = None) -> Out:
    return Out(ok=token or "")


@api.get("/optional-str-header")
def optional_str_header(request, h: Annotated[str, Header()] = "d") -> Out:
    return Out(ok=h)


# --- can produce a 422 ----------------------------------------------------


@api.get("/int-path/{n}")
def int_path(request, n: int) -> Out:
    return Out(ok=str(n))


@api.get("/int-query")
def int_query(request, n: int = 5) -> Out:
    return Out(ok=str(n))


@api.get("/required-str-query")
def required_str_query(request, token: str) -> Out:
    return Out(ok=token)


@api.get("/constrained-str-query")
def constrained_str_query(
    request, token: Annotated[str, Field(min_length=3)] = "abc"
) -> Out:
    return Out(ok=token)


@api.get("/validated-str-query")
def validated_str_query(request, token: Validated | None = None) -> Out:
    return Out(ok=token or "")


@api.post("/body")
def body(request, data: Payload) -> Out:
    return Out(ok=str(data.x))


CANNOT_422 = [
    ("get", "/no-params"),
    ("get", "/str-path/{hid}"),
    ("get", "/str-path-two/{hid}/{oid}"),
    ("get", "/optional-str-query"),
    ("get", "/optional-str-header"),
]

CAN_422 = [
    ("get", "/int-path/{n}"),
    ("get", "/int-query"),
    ("get", "/required-str-query"),
    ("get", "/constrained-str-query"),
    ("get", "/validated-str-query"),
    ("post", "/body"),
]


def _responses(method: str, path: str):
    schema = api.get_openapi_schema(path_prefix="")
    return schema["paths"][path][method]["responses"]


def test_infallible_params_omit_422():
    for method, path in CANNOT_422:
        assert 422 not in _responses(method, path), f"{method.upper()} {path}"


def test_fallible_params_document_422():
    for method, path in CAN_422:
        assert 422 in _responses(method, path), f"{method.upper()} {path}"


def test_documented_422s_are_actually_reachable():
    """The routes we still document a 422 for really do return one."""
    client = TestClient(api)
    assert client.get("/int-path/abc").status_code == 422
    assert client.get("/int-query", query_params={"n": "abc"}).status_code == 422
    assert client.get("/required-str-query").status_code == 422  # omitted
    assert (
        client.get("/constrained-str-query", query_params={"token": "a"}).status_code
        == 422
    )
    assert (
        client.get("/validated-str-query", query_params={"token": ""}).status_code
        == 422
    )
    assert client.post("/body", json={"x": "not-an-int"}).status_code == 422


def test_undocumented_422s_are_actually_unreachable():
    """The routes we dropped the 422 from can't be made to emit one."""
    client = TestClient(api)
    assert client.get("/no-params").status_code == 200
    # Values that would break a narrower type sail through an unconstrained str.
    # (An empty segment is excluded: it doesn't match the route at all, so it
    # never reaches validation in the first place.)
    for value in ("~!@$*()", "not-an-int", "0" * 500):
        assert client.get(f"/str-path/{value}").status_code == 200
    assert client.get("/optional-str-query").status_code == 200
    assert (
        client.get("/optional-str-query", query_params={"token": ""}).status_code == 200
    )
    assert client.get("/optional-str-header").status_code == 200
    assert client.get("/optional-str-header", headers={"h": ""}).status_code == 200


def test_query_schema_with_fallible_field_keeps_422():
    """A flattened Query[Schema] is judged by what it contains."""
    sub_api = HattoriAPI()

    class Filters(Schema):
        n: int = 0

    @sub_api.get("/filtered")
    def filtered(request, filters: Query[Filters]) -> Out:
        return Out(ok=str(filters.n))

    schema = sub_api.get_openapi_schema(path_prefix="")
    assert 422 in schema["paths"]["/filtered"]["get"]["responses"]
    assert (
        TestClient(sub_api).get("/filtered", query_params={"n": "x"}).status_code == 422
    )
