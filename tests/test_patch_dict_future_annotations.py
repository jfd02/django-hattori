"""PatchDict must work for schemas defined under ``from __future__ import annotations``.

With PEP 563 (stringized annotations) — which is extremely common — a schema's
raw ``__annotations__`` are strings. Building a patch schema must use the
type resolved by pydantic, not the raw string, otherwise ``str | None`` blows up.
"""

from __future__ import annotations

from hattori import HattoriAPI, Schema
from hattori.patch_dict import PatchDict, create_patch_schema
from hattori.testing import TestClient


class Item(Schema):
    name: str
    price: int
    tag: str | None = None


class PatchResult(Schema):
    payload: dict


def test_create_patch_schema_with_stringized_annotations():
    # This previously raised: TypeError: unsupported operand type(s) for |:
    # 'str' and 'NoneType'
    patch_cls = create_patch_schema(Item)
    wrapped = patch_cls._wrapped_model
    # Every field is optional now.
    for field in wrapped.model_fields.values():
        assert not field.is_required()


def test_patch_dict_endpoint_with_future_annotations():
    api = HattoriAPI()

    @api.patch("/item")
    def patch_item(request, payload: PatchDict[Item]) -> PatchResult:  # noqa: ARG001
        return {"payload": payload}

    client = TestClient(api)
    resp = client.patch("/item", json={"name": "new"})
    assert resp.status_code == 200
    # exclude_unset means only the provided key comes back.
    assert resp.json() == {"payload": {"name": "new"}}
