"""Test the APIReturn return type annotation pattern."""

from hattori import APIReturn, HattoriAPI, Schema
from hattori.testing import TestClient


class Item(Schema):
    name: str
    price: float


class Error(Schema):
    message: str


class NotFound(APIReturn[Error]):
    code = 404


class NoContent(APIReturn[None]):
    code = 204


api = HattoriAPI()


@api.get("/items")
def list_items(request) -> list[Item]:
    return [Item(name="Sword", price=9.99)]


@api.get("/items/{item_id}")
def get_item(request, item_id: int) -> Item | NotFound:
    if item_id == 0:
        return NotFound(Error(message="not found"))
    return Item(name="Sword", price=9.99)


@api.delete("/items/{item_id}")
def delete_item(request, item_id: int) -> NoContent:
    return NoContent(None)


client = TestClient(api)


def test_list_items():
    response = client.get("/items")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Sword"


def test_get_item_success():
    response = client.get("/items/1")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Sword"


def test_get_item_not_found():
    response = client.get("/items/0")
    assert response.status_code == 404
    data = response.json()
    assert data["message"] == "not found"


def test_delete_item():
    response = client.delete("/items/1")
    assert response.status_code == 204


def test_openapi_schema():
    schema = api.get_openapi_schema()
    items_get = schema["paths"]["/api/items"]["get"]
    assert 200 in items_get["responses"]

    item_get = schema["paths"]["/api/items/{item_id}"]["get"]
    assert 200 in item_get["responses"]
    assert 404 in item_get["responses"]

    item_delete = schema["paths"]["/api/items/{item_id}"]["delete"]
    assert 204 in item_delete["responses"]
