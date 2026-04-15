from copy import copy
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    TypeVar,
)

from pydantic import BaseModel
from pydantic_core import core_schema

from hattori import Body
from hattori.schema import Schema
from hattori.utils import is_optional_type


class ModelToDict(dict):
    _wrapped_model: Any = None
    _wrapped_model_dump_params: dict[str, Any] = {}

    @classmethod
    def __get_pydantic_core_schema__(cls, _source: Any, _handler: Any) -> Any:
        return core_schema.no_info_after_validator_function(
            cls._validate,
            cls._wrapped_model.__pydantic_core_schema__,
        )

    @classmethod
    def _validate(cls, input_value: Any) -> Any:
        return input_value.model_dump(**cls._wrapped_model_dump_params)


def get_schema_annotations(schema_cls: type[Any]) -> dict[str, Any]:
    annotations: dict[str, Any] = {}
    excluded_bases = {Schema, BaseModel}
    bases = schema_cls.mro()[:-1]
    final_bases = reversed([b for b in bases if b not in excluded_bases])

    for base in final_bases:
        annotations.update(getattr(base, "__annotations__", {}))

    return annotations


def create_patch_schema(schema_cls: type[Any]) -> type[ModelToDict]:
    schema_annotations = get_schema_annotations(schema_cls)
    values, annotations = {}, {}
    for f in schema_cls.model_fields.keys():
        t = schema_annotations[f]
        if not is_optional_type(t):
            field_info = copy(schema_cls.model_fields[f])
            field_info.default = None
            field_info.default_factory = None
            values[f] = field_info
            annotations[f] = t | None
    values["__annotations__"] = annotations
    OptionalSchema = type(f"{schema_cls.__name__}Patch", (schema_cls,), values)

    class OptionalDictSchema(ModelToDict):
        _wrapped_model = OptionalSchema
        _wrapped_model_dump_params = {"exclude_unset": True}

    return OptionalDictSchema


class PatchDictUtil:
    def __getitem__(self, schema_cls: Any) -> Any:
        new_cls = create_patch_schema(schema_cls)
        return Body[new_cls]  # type: ignore


if TYPE_CHECKING:  # pragma: nocover
    T = TypeVar("T")

    class PatchDict(dict[Any, Any], Generic[T]):
        pass

else:
    PatchDict = PatchDictUtil()
