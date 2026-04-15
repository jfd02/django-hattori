from typing import Any

from hattori.params.models import ParamModel


class _NestedParamModel(ParamModel):
    outer: dict[str, Any]
    leaf: int | None

    __hattori_flatten_map__ = {
        "foo": ("outer", "foo"),
        "bar": ("outer", "bar"),
        "leaf": ("leaf",),
    }


def test_map_data_paths_creates_parent_for_missing_nested_values():
    assert _NestedParamModel._map_data_paths({}) == {"outer": {}}


def test_map_data_paths_sets_values_when_present():
    data = _NestedParamModel._map_data_paths({"foo": 1, "leaf": 2})
    assert data == {"outer": {"foo": 1}, "leaf": 2}
