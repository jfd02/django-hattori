
from hattori import Schema


class Item(Schema):
    name: str
    description: str | None = None
    price: float
    quantity: int


class ItemUpdate(Schema):
    item_id: int
    item: Item


@api.put("/items/{item_id}", url_name="update_item_put")
def update(request, item_id: int, item: Item) -> ItemUpdate:
    return {"item_id": item_id, "item": item}
