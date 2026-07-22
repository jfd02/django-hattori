"""Tags declared on nested routers must accumulate through the whole chain.

``Router(tags=[...])`` sets a router's own tags; those should combine with the
tags of every ancestor router, at any nesting depth.
"""

from hattori import HattoriAPI, Router


def _tags(schema, path_suffix):
    path = next(p for p in schema["paths"] if p.endswith(path_suffix))
    return schema["paths"][path]["get"].get("tags")


def test_two_level_tag_accumulation():
    api = HattoriAPI()
    parent = Router(tags=["parent"])
    child = Router(tags=["child"])
    parent.add_router("/child", child)

    @child.get("/leaf")
    def leaf(request) -> None:  # noqa: ARG001
        pass

    api.add_router("/parent", parent)
    schema = api.get_openapi_schema()
    assert _tags(schema, "/leaf") == ["parent", "child"]


def test_three_level_tag_accumulation():
    api = HattoriAPI()
    r1 = Router(tags=["a"])
    r2 = Router(tags=["b"])
    r3 = Router(tags=["c"])
    r2.add_router("/r3", r3)
    r1.add_router("/r2", r2)

    @r3.get("/leaf")
    def leaf(request) -> None:  # noqa: ARG001
        pass

    api.add_router("/r1", r1)
    schema = api.get_openapi_schema()
    # Every ancestor tag must be present, in order.
    assert _tags(schema, "/leaf") == ["a", "b", "c"]
