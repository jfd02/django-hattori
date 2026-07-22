from copy import copy
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    TypeVar,
)

from pydantic_core import core_schema

from hattori import Body
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


def create_patch_schema(schema_cls: type[Any]) -> type[ModelToDict]:
    values, annotations = {}, {}
    for f, model_field in schema_cls.model_fields.items():
        # Use the annotation pydantic already resolved rather than the raw
        # ``__annotations__`` value, which under ``from __future__ import
        # annotations`` (PEP 563) is a *string* — and ``"str" | None`` raises.
        t = model_field.annotation
        field_info = copy(model_field)
        field_info.default = None
        field_info.default_factory = None
        values[f] = field_info
        # Already-nullable fields keep their annotation; non-nullable ones are
        # widened. Either way the default is cleared so every field is optional.
        annotations[f] = t if is_optional_type(t) else t | None
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
