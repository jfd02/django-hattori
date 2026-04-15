
from hattori import Schema


class Item(Schema):
    name: str
    description: str | None = None
    price: float
    quantity: int


@api.post("/items", url_name="create_item")
def create(request, item: Item) -> Item:
    return item
