"""Stateless API exercised through Django's real WSGI request handling."""

from typing import Annotated, Literal

from django.urls import path
from pydantic import BeforeValidator, ConfigDict, Field

from hattori import ApiError, Body, File, Form, HattoriAPI, Header, Query, Schema
from hattori.files import UploadedFile

api = HattoriAPI(urls_namespace="schemathesis")

# JSON Schema considers 1.0 an integer, whereas Pydantic strict mode does not.
# Match JSON's numeric semantics while still rejecting strings and booleans.
JsonInteger = Annotated[
    int,
    BeforeValidator(lambda v: int(v) if isinstance(v, float) and v.is_integer() else v),
]


class Payload(Schema):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=40)
    quantity: JsonInteger = Field(ge=0, le=100)
    tags: list[str] = Field(max_length=4)


@api.post("/body")
def body(request, payload: Payload) -> Payload:
    return payload


class Cat(Schema):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["cat"]
    lives: JsonInteger = Field(ge=1, le=9)


class Dog(Schema):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["dog"]
    name: str


class Pet(Schema):
    model_config = ConfigDict(extra="forbid")
    animal: Annotated[Cat | Dog, Field(discriminator="kind")]


@api.post("/union")
def union(request, pet: Pet) -> Pet:
    return pet


@api.get("/items/{item_id}")
def item(request, item_id: int, limit: int = Query(10, ge=1, le=100)) -> int:
    return item_id + limit


@api.get("/repeated")
def repeated(request, values: list[int] = Query(...)) -> list[int]:
    return values


@api.get("/csv")
def csv(request, values: list[int] = Query(..., explode=False)) -> list[int]:
    return values


@api.post("/form")
def form(request, text: str = Form(...), count: int = Form(0)) -> str:
    return f"{text}:{count}"


@api.post("/upload")
def upload(request, file: UploadedFile = File(...), text: str = Form("")) -> int:
    return file.size


class BusinessError(ApiError):
    code = 422
    error_code = "unavailable"
    message = "Item unavailable"


@api.get("/error")
def error(request, fail: bool = Query(False)) -> str | BusinessError:
    if fail:
        return BusinessError()
    return "ok"


@api.get("/async")
async def async_view(request, value: str = Query("")) -> str:
    return value


@api.post("/aliased-body")
def aliased_body(request, payload: Payload = Body(..., alias="item")) -> Payload:
    return payload


class Labelled(Schema):
    label: str = Field(validation_alias="tag", min_length=1, max_length=8)


@api.get("/aliased-query")
def aliased_query(request, labelled: Query[Labelled]) -> str:
    return labelled.label


@api.get("/header-list")
def header_list(request, tags: list[str] = Header(...)) -> list[str]:
    return tags


@api.get("/nullable", exclude_none=True)
def nullable(request) -> str | None:
    return None


urlpatterns = [path("api/", api.urls)]
