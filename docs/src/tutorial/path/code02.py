
from hattori import Schema


class ItemId(Schema):
    item_id: int


@api.get("/items/{item_id}")
def read_item(request, item_id: int) -> ItemId:
    return {"item_id": item_id}
