import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from ipaddress import IPv4Address, IPv6Address
from typing import Generic, TypeVar

import pytest
from django.http import HttpResponse
from django.utils.duration import duration_iso_string
from django.utils.translation import gettext_lazy
from pydantic import BaseModel, HttpUrl, ValidationError
from pydantic_core import Url

from hattori import Created, Router
from hattori.responses import JsonResponse, json_default, json_dumps
from hattori.testing import TestClient

router = Router()


@pytest.mark.parametrize("value", [-(2**64), -(2**63) - 1, 2**64, 10**100])
def test_large_integers_preserve_precision_and_other_json_encodings(value):
    class Model(BaseModel):
        number: int

    @dataclass
    class Record:
        number: int
        _private: str = "not serialized by orjson"

    payload = {
        "values": [value, {"nested": value}, (value,)],
        "model": Model(number=value),
        "record": Record(number=value),
        "date": datetime(2026, 1, 1, tzinfo=UTC),
        "decimal": Decimal("1.25"),
        "keys": {value: value, True: False},
    }
    assert json.loads(json_dumps(payload)) == {
        "values": [value, {"nested": value}, [value]],
        "model": {"number": value},
        "record": {"number": value},
        "date": "2026-01-01T00:00:00Z",
        "decimal": "1.25",
        "keys": {str(value): value, "true": False},
    }


def test_large_integer_response_through_http():
    from hattori import HattoriAPI

    api = HattoriAPI()

    @api.get("/integer")
    def integer(request, value: int) -> list[int]:
        return [value]

    value = 2**80 + 1
    response = TestClient(api).get(f"/integer?value={value}")
    assert response.status_code == 200
    assert json.loads(response.content) == [value]


def test_json_encoding_still_rejects_unsupported_objects():
    with pytest.raises(TypeError):
        json_dumps(object())
    with pytest.raises(TypeError):
        json_dumps([2**80, object()])


def test_large_integer_subclasses_cannot_inject_json():
    class Integer(int):
        def __str__(self):
            return "null"

    value = 2**80
    assert json.loads(json_dumps({Integer(value): Integer(value)})) == {
        str(value): value
    }


@router.get("/check_int")
def check_int(request) -> int:
    return "1"


@router.get("/check_int2")
def check_int2(request) -> int:
    return "str"


class MyEnum(Enum):
    first = "first"
    second = "second"


def to_camel(string: str) -> str:
    words = string.split("_")
    return words[0].lower() + "".join(word.capitalize() for word in words[1:])


class UserModel(BaseModel):
    id: int
    user_name: str
    # skipping password output to responses

    model_config = dict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


@router.get("/check_model")
def check_model(request) -> UserModel:
    return UserModel(id=1, user_name="John")


@router.get("/check_list_model")
def check_list_model(request) -> list[UserModel]:
    return [UserModel(id=1, user_name="John")]


@router.get("/check_model_alias", by_alias=True)
def check_model_alias(request) -> UserModel:
    return UserModel(id=1, user_name="John")


@router.get("/check_union")
def check_union(request, q: int) -> int | UserModel:
    if q == 0:
        return 1
    if q == 1:
        return UserModel(id=1, user_name="John")
    return "invalid"


class UserInternal(UserModel):
    password_hash: str


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]


def _internal_user() -> UserInternal:
    return UserInternal(id=1, user_name="John", password_hash="secret")


@router.get("/check_subclass")
def check_subclass(request) -> UserModel:
    return _internal_user()


@router.post("/check_subclass_created")
def check_subclass_created(request) -> Created[UserModel]:
    return Created(_internal_user())


@router.get("/check_subclass_list")
def check_subclass_list(request) -> list[UserModel]:
    return [_internal_user()]


@router.get("/check_subclass_in_generic")
def check_subclass_in_generic(request) -> Page[UserModel]:
    return Page[UserModel](items=[_internal_user()])


@router.get("/check_subclass_in_bare_generic")
def check_subclass_in_bare_generic(request) -> Page[UserModel]:
    return Page(items=[_internal_user()])


@router.get("/check_subclass_in_other_generic")
def check_subclass_in_other_generic(request) -> Page[UserModel]:
    return Page[UserInternal](items=[_internal_user()])


@router.get("/check_dicts_in_bare_generic")
def check_dicts_in_bare_generic(request) -> Page[UserModel]:
    return Page(items=[{"id": 1, "user_name": "John", "password_hash": "secret"}])


@router.get("/check_invalid_bare_generic")
def check_invalid_bare_generic(request) -> Page[UserModel]:
    return Page(items=["invalid"])


@router.get("/check_set_header")
def check_set_header(request, response: HttpResponse) -> int:
    response["Cache-Control"] = "no-cache"
    return 1


@router.get("/check_set_cookie")
def check_set_cookie(request, set: bool, response: HttpResponse) -> int:
    if set:
        response.set_cookie("test", "me")
    return 1


@router.get("/check_del_cookie")
def check_del_cookie(request, response: HttpResponse) -> int:
    response.delete_cookie("test")
    return 1


client = TestClient(router)


@pytest.mark.parametrize(
    "path,expected_response",
    [
        ("/check_int", 1),
        ("/check_model", {"id": 1, "user_name": "John"}),  # the password is skipped
        (
            "/check_list_model",
            [{"id": 1, "user_name": "John"}],
        ),  # the password is skipped
        ("/check_model", {"id": 1, "user_name": "John"}),  # the password is skipped
        ("/check_model_alias", {"id": 1, "userName": "John"}),  # result is camelCase
        ("/check_union?q=0", 1),
        ("/check_union?q=1", {"id": 1, "user_name": "John"}),
    ],
)
def test_responses(path, expected_response):
    response = client.get(path)
    assert response.status_code == 200, response.content
    assert response.json() == expected_response
    assert response.data == response.data == expected_response  # Ensures cache works


def test_validates():
    with pytest.raises(ValidationError):
        client.get("/check_int2")

    with pytest.raises(ValidationError):
        client.get("/check_union?q=2")

    # A generic that isn't the declared parameterization is validated against it.
    with pytest.raises(ValidationError):
        client.get("/check_invalid_bare_generic")


@pytest.mark.parametrize(
    "method,path,status,expected_response",
    [
        ("get", "/check_subclass", 200, {"id": 1, "user_name": "John"}),
        ("post", "/check_subclass_created", 201, {"id": 1, "user_name": "John"}),
        ("get", "/check_subclass_list", 200, [{"id": 1, "user_name": "John"}]),
        (
            "get",
            "/check_subclass_in_generic",
            200,
            {"items": [{"id": 1, "user_name": "John"}]},
        ),
        (
            "get",
            "/check_subclass_in_bare_generic",
            200,
            {"items": [{"id": 1, "user_name": "John"}]},
        ),
        (
            "get",
            "/check_subclass_in_other_generic",
            200,
            {"items": [{"id": 1, "user_name": "John"}]},
        ),
        (
            "get",
            "/check_dicts_in_bare_generic",
            200,
            {"items": [{"id": 1, "user_name": "John"}]},
        ),
    ],
)
def test_only_declared_fields_are_sent(method, path, status, expected_response):
    # Only the declared response type's fields go out, however the returned
    # instance was built.
    response = getattr(client, method)(path)
    assert response.status_code == status, response.content
    assert response.json() == expected_response


def test_set_header():
    response = client.get("/check_set_header")
    assert response.status_code == 200
    assert response.content == b"1"
    assert response["Cache-Control"] == "no-cache"


def test_set_cookie():
    response = client.get("/check_set_cookie?set=0")
    assert "test" not in response.cookies

    response = client.get("/check_set_cookie?set=1")
    cookie = response.cookies.get("test")
    assert cookie
    assert cookie.value == "me"


def test_del_cookie():
    response = client.get("/check_del_cookie")
    cookie = response.cookies.get("test")
    assert cookie
    assert cookie["expires"] == "Thu, 01 Jan 1970 00:00:00 GMT"
    assert cookie["max-age"] == 0


def test_ipv4address_encoding():
    data = {"ipv4": IPv4Address("127.0.0.1")}
    response = JsonResponse(data)
    response_data = json.loads(response.content)
    assert response_data["ipv4"] == str(data["ipv4"])


def test_ipv6address_encoding():
    data = {"ipv6": IPv6Address("::1")}
    response = JsonResponse(data)
    response_data = json.loads(response.content)
    assert response_data["ipv6"] == str(data["ipv6"])


def test_enum_encoding():
    data = {"enum": MyEnum.first}
    response = JsonResponse(data)
    response_data = json.loads(response.content)
    assert response_data["enum"] == data["enum"].value


def test_pydantic_url():
    data = {"url": Url("https://django-hattori.dev/")}
    response = JsonResponse(data)
    response_data = json.loads(response.content)
    assert response_data == {"url": "https://django-hattori.dev/"}


def test_pydantic_httpurl():
    # Regression: on pydantic >= 2.10, HttpUrl(...) no longer inherits from
    # pydantic_core.Url, so json_default needs to also recognize pydantic.AnyUrl.
    data = {"url": HttpUrl("https://django-hattori.dev/")}
    response = JsonResponse(data)
    response_data = json.loads(response.content)
    assert response_data == {"url": "https://django-hattori.dev/"}


class HttpUrlSchema(BaseModel):
    url: HttpUrl


@router.get("/check_httpurl")
def check_httpurl(request) -> HttpUrlSchema:
    return HttpUrlSchema(url="https://django-hattori.dev/")


def test_pydantic_httpurl_schema():
    response = client.get("/check_httpurl")
    assert response.status_code == 200
    assert response.json() == {"url": "https://django-hattori.dev/"}


def test_timedelta_encoding():
    value = timedelta(days=1, hours=2, minutes=3)
    response = JsonResponse({"d": value})
    response_data = json.loads(response.content)
    assert response_data == {"d": duration_iso_string(value)}


def test_decimal_encoding():
    response = JsonResponse({"price": Decimal("19.99")})
    response_data = json.loads(response.content)
    assert response_data == {"price": "19.99"}


def test_lazy_string_encoding():
    # Django lazy strings (e.g. gettext_lazy) are Promise instances and must
    # serialize to their resolved text.
    response = JsonResponse({"label": gettext_lazy("hello")})
    response_data = json.loads(response.content)
    assert response_data == {"label": "hello"}


def test_unsupported_type_raises_type_error():
    class NotSerializable:
        pass

    with pytest.raises(TypeError):
        json_default(NotSerializable())
