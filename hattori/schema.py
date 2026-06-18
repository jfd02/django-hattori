"""
`Schema` is a thin alias for `pydantic.BaseModel`, kept separate because "Model"
is already overloaded in Django for ORM models.

Schemas are *strict data types*: they validate from dicts and accept explicit
kwargs. Unlike pydantic's `from_attributes=True`, hattori does **not** coerce
arbitrary Django ORM instances into schemas automatically. If you want to turn
a `User` model into a `UserSchema`, construct it explicitly:

    return UserSchema(id=user.id, username=user.username, email=user.email)

That coupling is the user's to own, not the framework's.
"""

from enum import Enum
from typing import (
    Any,
    Literal,
    dataclass_transform,
    get_args,
    get_origin,
    no_type_check,
)

import pydantic
from pydantic import BaseModel, ConfigDict, Field
from pydantic._internal._model_construction import ModelMetaclass
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue

pydantic_version = list(map(int, pydantic.VERSION.split(".")[:2]))

__all__ = ["BaseModel", "Field", "Schema"]


class HattoriGenerateJsonSchema(GenerateJsonSchema):
    def default_schema(self, schema: Any) -> JsonSchemaValue:
        # Pydantic default renders null's and default_factory's, which breaks
        # swagger and django model callable defaults. Override accordingly.
        json_schema = self.generate_inner(schema["schema"])

        default = None
        if "default" in schema and schema["default"] is not None:
            default = self.encode_default(schema["default"])

        if "$ref" in json_schema:
            result = {"allOf": [json_schema]}
        else:
            result = json_schema

        if default is not None:
            result["default"] = default

        return result


def _find_model_ref(schema: Any, model: type) -> str | None:
    """Return the core-schema ref of ``model``'s own model-schema node.

    Only the node whose ``cls`` is ``model`` itself is matched, so nested
    model fields (which carry their own refs) are ignored.
    """
    if isinstance(schema, dict):
        if schema.get("type") == "model" and schema.get("cls") is model:
            return schema.get("ref")
        for v in schema.values():
            if isinstance(v, (dict, list)):
                found = _find_model_ref(v, model)
                if found is not None:
                    return found
    elif isinstance(schema, list):
        for item in schema:
            found = _find_model_ref(item, model)
            if found is not None:
                return found
    return None


def _rename_core_schema_ref(schema: Any, old_ref: str, name: str) -> None:
    """Rename only ``old_ref`` to ``name`` across a Pydantic core schema tree.

    Touches both ``ref`` definitions and the ``schema_ref`` keys that point
    back to them (e.g. self-referential models), while leaving every other
    model's ref untouched so nested schemas are not clobbered.
    """
    if isinstance(schema, dict):
        if schema.get("ref") == old_ref:
            schema["ref"] = name
        if schema.get("schema_ref") == old_ref:
            schema["schema_ref"] = name
        for v in schema.values():
            if isinstance(v, (dict, list)):
                _rename_core_schema_ref(v, old_ref, name)
    elif isinstance(schema, list):
        for item in schema:
            _rename_core_schema_ref(item, old_ref, name)


@dataclass_transform(kw_only_default=True, field_specifiers=(Field,))
class _SchemaMetaclass(ModelMetaclass):
    @no_type_check
    def __new__(cls, name, bases, namespace, **kwargs):
        return super().__new__(cls, name, bases, namespace, **kwargs)


class Schema(BaseModel, metaclass=_SchemaMetaclass):
    model_config = ConfigDict()

    def __class_getitem__(cls, params: Any) -> Any:
        """Parameterize and produce clean schema names for OpenAPI.

        Pydantic generates ugly $defs names for parameterized generics
        (e.g. ``ErrorResponse_Literal_not_found__``). This override
        renames parameterized models to use the Literal values directly
        (e.g. ``ErrorResponse_not_found``).

        Only renames when all type args are Literal values or enum members;
        plain type args like ``str`` keep Pydantic's default naming.
        """
        model = super().__class_getitem__(params)
        if not isinstance(model, type):
            return model

        param_list = params if isinstance(params, tuple) else (params,)
        values: list[str] = []
        for p in param_list:
            if get_origin(p) is Literal:
                for a in get_args(p):
                    values.append(a.value if isinstance(a, Enum) else str(a))
            elif isinstance(p, Enum):
                values.append(p.value)

        if values:
            name = cls.__name__ + "_" + "_".join(values)
            model.__name__ = name
            model.__qualname__ = name
            old_ref = _find_model_ref(model.__pydantic_core_schema__, model)
            if old_ref is not None:
                _rename_core_schema_ref(model.__pydantic_core_schema__, old_ref, name)

        return model

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return cls.model_json_schema(schema_generator=HattoriGenerateJsonSchema)
