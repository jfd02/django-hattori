from copy import copy
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    TypeVar,
    get_args,
)

from pydantic_core import core_schema

from hattori import Body

__all__ = ["PatchDict", "PatchName", "create_patch_schema"]


@dataclass(frozen=True)
class PatchName:
    """Name the schema ``PatchDict`` generates, independently of the source class.

    By default the generated model is named ``<SourceSchema>Patch``, which puts
    an internal class name in the OpenAPI document and in every generated
    client. Annotate the source schema to set it explicitly::

        payload: PatchDict[Annotated[FlagsSchema, PatchName("UserFlags")]]

    The name is used verbatim — no ``Patch`` suffix is appended — so renaming
    ``FlagsSchema`` no longer renames a client-facing type.
    """

    name: str


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


def create_patch_schema(
    schema_cls: type[Any], *, name: str | None = None
) -> type[ModelToDict]:
    """Build the all-omittable ``dict``-producing model behind ``PatchDict``.

    ``name`` overrides the generated model's name (and so its OpenAPI
    component name), which otherwise defaults to ``f"{schema_cls.__name__}Patch"``.
    """
    values, annotations = {}, {}
    for f, model_field in schema_cls.model_fields.items():
        field_info = copy(model_field)
        # A default makes the field omittable without widening its type, so an
        # explicit ``null`` is still rejected unless the source schema allows
        # it. The default itself is never validated or seen: it would fail a
        # non-nullable annotation, and ``exclude_unset`` drops it on dump.
        field_info.default = None
        field_info.default_factory = None
        field_info.validate_default = False
        values[f] = field_info
        # Use the annotation pydantic already resolved rather than the raw
        # ``__annotations__`` value, which under ``from __future__ import
        # annotations`` (PEP 563) is a *string*.
        annotations[f] = model_field.annotation
    values["__annotations__"] = annotations
    schema_name = name if name is not None else f"{schema_cls.__name__}Patch"
    OptionalSchema = type(schema_name, (schema_cls,), values)

    class OptionalDictSchema(ModelToDict):
        _wrapped_model = OptionalSchema
        _wrapped_model_dump_params = {"exclude_unset": True}

    return OptionalDictSchema


def _unwrap_annotated(item: Any) -> tuple[Any, str | None]:
    """Split ``Annotated[Schema, PatchName(...)]`` into the schema and the name."""
    if not hasattr(item, "__metadata__"):
        return item, None
    schema_cls, *metadata = get_args(item)
    name = next(
        (m.name for m in reversed(metadata) if isinstance(m, PatchName)),
        None,
    )
    return schema_cls, name


class PatchDictUtil:
    def __getitem__(self, item: Any) -> Any:
        schema_cls, name = _unwrap_annotated(item)
        new_cls = create_patch_schema(schema_cls, name=name)
        return Body[new_cls]  # type: ignore


if TYPE_CHECKING:  # pragma: nocover
    T = TypeVar("T")

    class PatchDict(dict[Any, Any], Generic[T]):
        pass

else:
    PatchDict = PatchDictUtil()
