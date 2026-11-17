"""A generic ``type`` alias is read like the type it names, its parameters filled in.

What the alias wraps its parameter in need not be hashable: ``Annotated``
metadata may hold a dict, and a ``Callable`` lists its parameters.
"""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import Discriminator, Tag

from hattori import Body, HattoriAPI, Schema
from hattori.returns import alias_value
from hattori.testing import TestClient


class Dog(Schema):
    kind: Literal["dog"]
    bark: str


class Cat(Schema):
    kind: Literal["cat"]
    meow: str


def _kind(value: Any) -> Any:
    return value.get("kind") if isinstance(value, dict) else value.kind


type Tagged[T] = Annotated[
    T,
    Discriminator(
        _kind,
        custom_error_type="invalid_pet",
        custom_error_message="Expected {kinds}",
        custom_error_context={"kinds": "dog or cat"},
    ),
]
type Pet = Tagged[Annotated[Dog, Tag("dog")] | Annotated[Cat, Tag("cat")]]
type Handler[T] = Callable[[T], int]


def test_param_whose_alias_holds_unhashable_metadata_is_read():
    api = HattoriAPI()

    @api.post("/pet")
    def adopt(request, pet: Pet = Body(...)) -> dict[str, str]:
        return {"kind": pet.kind}

    client = TestClient(api)

    assert client.post("/pet", json={"kind": "cat", "meow": "hi"}).json() == {
        "kind": "cat"
    }
    response = client.post("/pet", json={"kind": "cow"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == "Expected dog or cat"


def test_alias_of_a_callable_fills_in_its_parameters():
    assert alias_value(Handler[str]) == Callable[[str], int]
