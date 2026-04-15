
from hattori import Form, Schema


class Item(Schema):
    name: str
    description: str | None = None
    price: float
    quantity: int


@api.post("/items")
def create(request, item: Form[Item]) -> Item:
    return item
