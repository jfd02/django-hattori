"""
Since "Model" word would be very confusing when used in django context, this
module basically makes an alias for it named "Schema" and adds extra whistles to
be able to work with django querysets and managers.

The schema is a bit smarter than a standard pydantic Model because it can handle
dotted attributes and resolver methods. For example::


    class UserSchema(User):
        name: str
        initials: str
        boss: str = Field(None, alias="boss.first_name")

        @staticmethod
        def resolve_name(obj):
            return f"{obj.first_name} {obj.last_name}"

"""

import warnings
from enum import Enum
from typing import (
    Any,
    Callable,
    Literal,
    TypeVar,
    get_args,
    get_origin,
    no_type_check,
)

import pydantic
from django.db.models import Manager, QuerySet
from django.db.models.fields.files import FieldFile
from django.template import Variable, VariableDoesNotExist
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator
from pydantic._internal._model_construction import ModelMetaclass
from pydantic.functional_validators import ModelWrapValidatorHandler
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from typing_extensions import dataclass_transform

from hattori.signature.utils import get_args_names, has_kwargs

pydantic_version = list(map(int, pydantic.VERSION.split(".")[:2]))

__all__ = ["BaseModel", "Field", "DjangoGetter", "Schema"]

S = TypeVar("S", bound="Schema")


class DjangoGetter:
    __slots__ = ("_obj", "_schema_cls", "_context", "__dict__")

    def __init__(self, obj: Any, schema_cls: type[S], context: Any = None):
        self._obj = obj
        self._schema_cls = schema_cls
        self._context = context

    def __getattr__(self, key: str) -> Any:
        resolver = self._schema_cls._hattori_resolvers.get(key)
        if resolver:
            value = resolver(getter=self)
        else:
            if isinstance(self._obj, dict):
                if key not in self._obj:
                    raise AttributeError(key)
                value = self._obj[key]
            else:
                try:
                    value = getattr(self._obj, key)
                except AttributeError:
                    try:
                        value = Variable(key).resolve(self._obj)
                        # TODO: Variable(key) __init__ is actually slower than
                        #       Variable.resolve - so it better be cached
                    except VariableDoesNotExist as e:
                        raise AttributeError(key) from e
        return self._convert_result(value)

    def _convert_result(self, result: Any) -> Any:
        if isinstance(result, Manager):
            return list(result.all())

        elif isinstance(result, getattr(QuerySet, "__origin__", QuerySet)):
            return list(result)

        if callable(result):
            if getattr(result, "alters_data", False):
                raise AttributeError
            return result()

        elif isinstance(result, FieldFile):
            if not result:
                return None
            return result.url

        return result

    def __repr__(self) -> str:
        return f"<DjangoGetter: {repr(self._obj)}>"


class Resolver:
    __slots__ = ("_func", "_static", "_takes_context")
    _static: bool
    _func: Any
    _takes_context: bool

    def __init__(self, func: Callable | staticmethod):
        if isinstance(func, staticmethod):
            self._static = True
            self._func = func.__func__
        else:
            self._static = False
            self._func = func

        arg_names = get_args_names(self._func)
        self._takes_context = has_kwargs(self._func) or "context" in arg_names

    def __call__(self, getter: DjangoGetter) -> Any:
        kwargs = {}
        if self._takes_context:
            kwargs["context"] = getter._context

        if self._static:
            return self._func(getter._obj, **kwargs)
        raise NotImplementedError(
            "Non static resolves are not supported yet"
        )  # pragma: no cover


@dataclass_transform(kw_only_default=True, field_specifiers=(Field,))
class ResolverMetaclass(ModelMetaclass):
    _hattori_resolvers: dict[str, Resolver]

    @no_type_check
    def __new__(cls, name, bases, namespace, **kwargs):
        resolvers = {}

        for base in reversed(bases):
            base_resolvers = getattr(base, "_hattori_resolvers", None)
            if base_resolvers:
                resolvers.update(base_resolvers)
        for attr, resolve_func in namespace.items():
            if not attr.startswith("resolve_"):
                continue
            if not callable(resolve_func) and not isinstance(
                resolve_func, staticmethod
            ):
                continue  # pragma: no cover
            resolvers[attr[8:]] = Resolver(resolve_func)

        result = super().__new__(cls, name, bases, namespace, **kwargs)
        result._hattori_resolvers = resolvers
        return result


class HattoriGenerateJsonSchema(GenerateJsonSchema):
    def default_schema(self, schema: Any) -> JsonSchemaValue:
        # Pydantic default actually renders null's and default_factory's
        # which really breaks swagger and django model callable defaults
        # so here we completely override behavior
        json_schema = self.generate_inner(schema["schema"])

        default = None
        if "default" in schema and schema["default"] is not None:
            default = self.encode_default(schema["default"])

        if "$ref" in json_schema:
            # Since reference schemas do not support child keys, we wrap the reference schema in a single-case allOf:
            result = {"allOf": [json_schema]}
        else:
            result = json_schema

        if default is not None:
            result["default"] = default

        return result


def _update_core_schema_ref(schema: Any, name: str) -> None:
    """Recursively update 'ref' keys in a Pydantic core schema dict tree."""
    if isinstance(schema, dict):
        if "ref" in schema:
            schema["ref"] = name
        for v in schema.values():
            if isinstance(v, (dict, list)):
                _update_core_schema_ref(v, name)
    elif isinstance(schema, list):
        for item in schema:
            _update_core_schema_ref(item, name)


class Schema(BaseModel, metaclass=ResolverMetaclass):
    model_config = ConfigDict(from_attributes=True)

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

        # Collect Literal values from all type params
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
            _update_core_schema_ref(model.__pydantic_core_schema__, name)

        return model

    @model_validator(mode="wrap")
    @classmethod
    def _run_root_validator(
        cls, values: Any, handler: ModelWrapValidatorHandler[S], info: ValidationInfo
    ) -> Any:
        # If Pydantic intends to validate against the __dict__ of the immediate Schema
        # object, then we need to call `handler` directly on `values` before the conversion
        # to DjangoGetter, since any checks or modifications on DjangoGetter's __dict__
        # will not persist to the original object.
        forbids_extra = cls.model_config.get("extra") == "forbid"
        should_validate_assignment = cls.model_config.get("validate_assignment", False)
        if forbids_extra or should_validate_assignment:
            handler(values)

        values = DjangoGetter(values, cls, info.context)
        return handler(values)

    @classmethod
    def from_orm(cls: type[S], obj: Any, **kw: Any) -> S:
        return cls.model_validate(obj, **kw)

    def dict(self, *a: Any, **kw: Any) -> dict[str, Any]:
        "Backward compatibility with pydantic 1.x"
        return self.model_dump(*a, **kw)

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return cls.model_json_schema(schema_generator=HattoriGenerateJsonSchema)

    @classmethod
    def schema(cls) -> dict[str, Any]:  # type: ignore
        warnings.warn(
            ".schema() is deprecated, use .json_schema() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.json_schema()
