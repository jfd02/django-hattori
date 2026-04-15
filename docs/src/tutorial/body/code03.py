
from hattori import Schema


class Item(Schema):
    name: str
    description: str | None = None
    price: float
    quantity: int


class ItemUpdateResponse(Schema):
    item_id: int
    item: Item
    q: str


@api.post("/items/{item_id}", url_name="update_item_post")
def update(request, item_id: int, item: Item, q: str) -> ItemUpdateResponse:
    return {"item_id": item_id, "item": item, "q": q}
