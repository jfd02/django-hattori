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
from weakref import WeakValueDictionary

import pydantic
from pydantic import BaseModel, ConfigDict, Field
from pydantic._internal._core_utils import CoreSchemaField, get_type_ref
from pydantic._internal._model_construction import ModelMetaclass
from pydantic.json_schema import (
    CoreModeRef,
    DefsRef,
    GenerateJsonSchema,
    JsonSchemaValue,
)

pydantic_version = list(map(int, pydantic.VERSION.split(".")[:2]))

__all__ = ["BaseModel", "Field", "Schema"]


# Keys whose value is a payload rather than schema, so its own keys are data.
_PAYLOAD_KEYWORDS = frozenset({"default", "example", "examples"})


def _sorted_schema(value: Any, parent_key: str | None) -> Any:
    if parent_key in _PAYLOAD_KEYWORDS:
        return value
    if isinstance(value, dict):
        keys = value if parent_key == "properties" else sorted(value)
        return {key: _sorted_schema(value[key], key) for key in keys}
    if isinstance(value, list):
        return [_sorted_schema(item, parent_key) for item in value]
    return value


# Schemas that declare their own `title`, keyed by the core ref Pydantic names
# their definition from. Weak, so a class defined inside a function does not
# outlive it and lend its title to a later class that reuses its id.
_TITLED_SCHEMAS: WeakValueDictionary[str, type[BaseModel]] = WeakValueDictionary()


class HattoriGenerateJsonSchema(GenerateJsonSchema):
    def get_defs_ref(self, core_mode_ref: CoreModeRef) -> DefsRef:
        # A schema that declares a title is defined under it, so its component
        # is named what its `title` says rather than after the Python class.
        # The title goes ahead of Pydantic's own choices, which stay as the
        # fallbacks when two schemas want the same name.
        defs_ref = super().get_defs_ref(core_mode_ref)
        model = _TITLED_SCHEMAS.get(core_mode_ref[0])
        title = model.model_config.get("title") if model is not None else None
        if title:
            choices = self._prioritized_defsref_choices[defs_ref]
            # Pydantic's first two choices are the bare name and the name with
            # the mode suffix (`-Input` or `-Output`) it adds when a schema
            # differs between validation and serialization.
            mode_suffix = choices[1].removeprefix(choices[0])
            name = self.normalize_name(title)
            choices[:0] = [DefsRef(name), DefsRef(name + mode_suffix)]
        return defs_ref

    def _get_alias_name(self, field: CoreSchemaField, name: str) -> str:
        # A model that does not validate by alias (`validate_by_alias=False`)
        # reads each field by its name, so that is the name it documents.
        # Pydantic names it by its validation alias whatever the config says.
        if (
            self.mode == "validation"
            and field["type"] != "computed-field"
            and not self._config.validate_by_alias
        ):
            return name
        return super()._get_alias_name(field, name)

    def sort(
        self, value: JsonSchemaValue, parent_key: str | None = None
    ) -> JsonSchemaValue:
        # Pydantic sorts every key alphabetically, sparing only `properties` and
        # the top level of a `default`. An example object would come out with
        # its fields in a different order from the model it is an example of.
        sorted_schema: JsonSchemaValue = _sorted_schema(value, parent_key)
        return sorted_schema

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
        # A title names only the class that declares it. Pydantic copies
        # `model_config`, title included, into every subclass, which is a
        # different schema, so a subclass that declares none goes back to its
        # class name. Read before Pydantic writes the merged config back into
        # `namespace`.
        titled = "title" in kwargs or "title" in namespace.get("model_config", {})
        if not titled and any(
            getattr(base, "model_config", {}).get("title") for base in bases
        ):
            kwargs["title"] = None
        model = super().__new__(cls, name, bases, namespace, **kwargs)
        if titled:
            _TITLED_SCHEMAS[get_type_ref(model)] = model
        return model


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
                    # str() every part: an enum with a non-string value (e.g. an
                    # IntEnum status code) would otherwise break "_".join below.
                    values.append(str(a.value) if isinstance(a, Enum) else str(a))
            elif isinstance(p, Enum):
                values.append(str(p.value))

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
