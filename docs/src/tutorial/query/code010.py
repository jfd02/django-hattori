
from hattori import Query, Schema


class Filters(Schema):
    limit: int = 100
    offset: int | None = None
    query: str | None = None
    categories: list[str] | None = None


class FilterResponse(Schema):
    filters: Filters


@api.get("/filter")
def events(request, filters: Query[Filters]) -> FilterResponse:
    return {"filters": filters}
