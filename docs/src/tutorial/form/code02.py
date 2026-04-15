
from hattori import Form, Schema


class Item(Schema):
    name: str
    description: str | None = None
    price: float
    quantity: int


class ItemUpdateResponse(Schema):
    item_id: int
    item: Item
    q: str


@api.post("/items/{item_id}")
def update(
    request, item_id: int, q: str, item: Form[Item]
) -> ItemUpdateResponse:
    return {"item_id": item_id, "item": item, "q": q}
