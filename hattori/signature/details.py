import inspect
import warnings
from collections import defaultdict, namedtuple
from collections.abc import Callable, Generator
from typing import Annotated, Any, get_args, get_origin

import pydantic
from django.http import HttpResponse
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from hattori import UploadedFile
from hattori.compatibility.util import UNION_TYPES
from hattori.errors import ConfigError
from hattori.params.models import (
    Body,
    File,
    Form,
    Param,
    Path,
    Query,
    TModel,
    TModels,
    _MultiPartBody,
    is_comma_separated,
)
from hattori.returns import alias_value, is_alias
from hattori.signature.utils import get_path_param_names, get_typed_signature
from hattori.utils import is_optional_type

__all__ = [
    "ViewSignature",
    "is_pydantic_model",
    "is_collection_type",
    "detect_collection_fields",
]

FuncParam = namedtuple(
    "FuncParam", ["name", "alias", "source", "annotation", "is_collection"]
)


def _read_name(field: FieldInfo, name: str, by_alias: bool = True) -> str:
    """The name ``field`` is read from a request by.

    Its validation alias, where it has a plain one, else its alias, else the
    name it is declared under. A model that does not validate by alias
    (``validate_by_alias=False``) reads every field by its name.
    """
    if not by_alias:
        return name
    alias = field.validation_alias
    return alias if isinstance(alias, str) else field.alias or name


def _reads_by_alias(model: Any) -> bool:
    by_alias: bool = model.model_config.get("validate_by_alias", True)
    return by_alias


class ViewSignature:
    FLATTEN_PATH_SEP = (
        "\x1e"  # ASCII Record Separator.  IE: not generally used in query names
    )
    response_arg: str | None = None

    def __init__(self, path: str, view_func: Callable[..., Any]) -> None:
        self.view_func = view_func
        self.signature = get_typed_signature(self.view_func)
        self.path = path
        self.path_params_names = get_path_param_names(path)
        self.docstring = inspect.cleandoc(view_func.__doc__ or "")

        self.params = []
        for name, arg in self.signature.parameters.items():
            if name == "request":
                # TODO: maybe better assert that 1st param is request or check by type?
                # maybe even have attribute like `has_request`
                # so that users can ignore passing request if not needed
                continue

            if arg.kind == arg.VAR_KEYWORD:
                # Skipping **kwargs
                continue

            if arg.kind == arg.VAR_POSITIONAL:
                # Skipping *args
                continue

            if arg.annotation is HttpResponse:
                self.response_arg = name
                continue

            if (
                arg.annotation is inspect.Parameter.empty
                and isinstance(arg.default, type)
                and issubclass(arg.default, pydantic.BaseModel)
            ):
                raise ConfigError(
                    f"Looks like you are using `{name}={arg.default.__name__}` instead of `{name}: {arg.default.__name__}` (annotation)"
                )

            func_param = self._get_param_type(name, arg)
            self.params.append(func_param)

        if hasattr(view_func, "_hattori_contribute_args"):
            # _hattori_contribute_args is a special attribute
            # which allows developers to create custom function params
            # inside decorators or other functions
            for p_name, p_type, p_source in view_func._hattori_contribute_args:
                self.params.append(
                    FuncParam(
                        p_name, _read_name(p_source, p_name), p_source, p_type, False
                    )
                )

        self.models: TModels = self._create_models()

        self._validate_view_path_params()

    def _validate_view_path_params(self) -> None:
        """verify all path params are present in the path model fields"""
        if self.path_params_names:
            path_model = next(
                (m for m in self.models if m.__hattori_param_source__ == "path"), None
            )
            missing = tuple(
                sorted(
                    name
                    for name in self.path_params_names
                    if not (path_model and name in path_model.__hattori_flatten_map__)
                )
            )
            if missing:
                warnings.warn_explicit(
                    UserWarning(
                        f"Field(s) {missing} are in the view path, but were not found in the view signature."
                    ),
                    category=None,
                    filename=inspect.getfile(self.view_func),
                    lineno=inspect.getsourcelines(self.view_func)[1],
                    source=None,
                )

    def _create_models(self) -> TModels:
        params_by_source_cls: dict[Any, list[FuncParam]] = defaultdict(list)
        for param in self.params:
            param_source_cls = type(param.source)
            params_by_source_cls[param_source_cls].append(param)

        is_multipart_response_with_body = Body in params_by_source_cls and (
            File in params_by_source_cls or Form in params_by_source_cls
        )
        if is_multipart_response_with_body:
            params_by_source_cls[_MultiPartBody] = params_by_source_cls.pop(Body)

        result = []
        for param_cls, args in params_by_source_cls.items():
            cls_name: str = param_cls.__name__ + "Params"
            attrs = {i.name: i.source for i in args}
            attrs["__hattori_param_source__"] = param_cls._param_source()
            attrs["__hattori_flatten_map_reverse__"] = {}

            if attrs["__hattori_param_source__"] == "file":
                pass

            elif attrs["__hattori_param_source__"] in {
                "form",
                "query",
                "header",
                "cookie",
                "path",
            }:
                flatten_map = self._args_flatten_map(args)
                attrs["__hattori_flatten_map__"] = flatten_map
                attrs["__hattori_nullable_models__"] = [
                    path
                    for arg in args
                    if is_pydantic_model(arg.annotation)
                    for path in _nullable_model_paths(
                        arg.annotation, arg.source, (arg.alias,)
                    )
                ]
                attrs["__hattori_flatten_map_reverse__"] = {
                    v: (k,) for k, v in flatten_map.items()
                }

            else:
                assert attrs["__hattori_param_source__"] == "body"
                if is_multipart_response_with_body:
                    attrs["__hattori_body_params__"] = {
                        i.alias: is_multipart_json_part(i.annotation) for i in args
                    }
                else:
                    attrs["__read_from_single_attr__"] = (
                        self._single_body_key(args[0]) if len(args) == 1 else None
                    )

            # adding annotations
            attrs["__annotations__"] = {i.name: i.annotation for i in args}

            # collection fields:
            flatten_map = attrs.get("__hattori_flatten_map__", {})
            attrs["__hattori_collection_fields__"] = detect_collection_fields(
                args, flatten_map
            )

            # csv fields (explode=False), declared on the argument itself or on
            # a field of a schema argument:
            attrs["__hattori_csv_fields__"] = [
                i.alias or i.name
                for i in args
                if i.is_collection and is_comma_separated(i.source)
            ] + [
                name
                for name, field in _flattened_fields(args, flatten_map)
                if is_collection_type(field.annotation) and is_comma_separated(field)
            ]

            source = attrs["__hattori_param_source__"]
            if source == "cookie":
                # A cookie has one value, so a list in it is comma-separated
                # whatever explode says, and is documented so.
                attrs["__hattori_csv_fields__"] = attrs["__hattori_collection_fields__"]
            elif source in ("header", "path"):
                # So do a header and a path, but the simple style OpenAPI gives
                # them already says so, and form is not one either may take.
                attrs["__hattori_csv_fields__"] = []

            base_cls = param_cls._model
            model_cls = type(cls_name, (base_cls,), attrs)
            # TODO: https://pydantic-docs.helpmanual.io/usage/models/#dynamic-model-creation - check if anything special in create_model method that I did not use
            result.append(model_cls)
        return result

    @staticmethod
    def _single_body_key(arg: FuncParam) -> str:
        """The key a body that is one parameter is handed to pydantic under.

        The body is that parameter's value, so the key names nothing a client
        sends: it only has to be one pydantic reads the parameter by. That is
        its validation alias where it has a plain one, and its own name
        otherwise.
        """
        alias = arg.source.validation_alias
        return alias if isinstance(alias, str) else arg.name

    def _args_flatten_map(self, args: list[FuncParam]) -> dict[str, tuple[str, ...]]:
        flatten_map = {}
        arg_names: Any = {}
        for arg in args:
            if is_pydantic_model(arg.annotation):
                for name, path in self._model_flatten_map(arg.annotation, arg.alias):
                    if name in flatten_map:
                        raise ConfigError(
                            f"Duplicated name: '{name}' in params: '{arg_names[name]}' & '{arg.name}'"
                        )
                    flatten_map[name] = tuple(path.split(self.FLATTEN_PATH_SEP))
                    arg_names[name] = arg.name
            else:
                name = arg.alias
                if name in flatten_map:
                    raise ConfigError(
                        f"Duplicated name: '{name}' also in '{arg_names[name]}'"
                    )
                flatten_map[name] = (name,)
                arg_names[name] = name

        return flatten_map

    def _model_flatten_map(self, model: TModel, prefix: str) -> Generator:
        model = _unwrap_union_model(model)
        field: FieldInfo
        by_alias = _reads_by_alias(model)
        for attr, field in model.model_fields.items():
            field_name = _read_name(field, attr, by_alias)
            name = f"{prefix}{self.FLATTEN_PATH_SEP}{field_name}"
            if is_pydantic_model(field.annotation):
                yield from self._model_flatten_map(field.annotation, name)  # type: ignore
            else:
                yield field_name, name

    def _get_param_type(self, name: str, arg: inspect.Parameter) -> FuncParam:
        annotation = arg.annotation
        default = arg.default

        if get_origin(annotation) is Annotated:
            args = get_args(annotation)
            if isinstance(args[-1], Param):
                prev_default = default
                if len(args) == 2:
                    annotation, default = args
                else:
                    annotation, default = Annotated[args[:-1]], args[-1]
                if prev_default != self.signature.empty:
                    default.default = prev_default

        if annotation == self.signature.empty:
            if default == self.signature.empty:
                annotation = str
            else:
                if isinstance(default, Param):
                    annotation = type(default.default)
                else:
                    annotation = type(default)

            if annotation == PydanticUndefined.__class__:
                # TODO: ^ check why is that so
                annotation = str

        if annotation == type(None) or annotation == type(Ellipsis):  # noqa
            annotation = str

        is_collection = is_collection_type(annotation)
        named = _unalias(annotation)

        if named == UploadedFile or (
            is_collection and getattr(named, "__args__", (None,))[0] == UploadedFile
        ):
            # People often forgot to mark UploadedFile as a File, so we better assign it automatically
            if default == self.signature.empty or default is None:
                default = default == self.signature.empty and ... or default
                return FuncParam(name, name, File(default), annotation, is_collection)

        # 1) if type of the param is defined as one of the Param's subclasses - we just use that definition
        if isinstance(default, Param):
            param_source = default

        # 2) if param name is a part of the path parameter
        elif name in self.path_params_names:
            assert default == self.signature.empty, (
                f"'{name}' is a path param, default not allowed"
            )
            param_source = Path(...)

        # 3) if param is a collection, or annotation is part of pydantic model:
        elif is_collection or is_pydantic_model(annotation):
            if default == self.signature.empty:
                param_source = Body(...)
            else:
                param_source = Body(default)

        # 4) the last case is query param
        else:
            if default == self.signature.empty:
                param_source = Query(...)
            else:
                param_source = Query(default)

        # If default is None but annotation is not Optional,
        # wrap it in Optional to allow None values in Pydantic v2
        if default is None and not is_optional_type(annotation):
            annotation = annotation | None

        return FuncParam(
            name,
            _read_name(param_source, name),
            param_source,
            annotation,
            is_collection,
        )


def _unalias(annotation: Any) -> Any:
    """What ``annotation`` names, through any ``type`` aliases it is written as."""
    while is_alias(annotation):
        annotation = alias_value(annotation)
    return annotation


def _allows_none(annotation: Any) -> bool:
    annotation = _unalias(annotation)
    return get_origin(annotation) in UNION_TYPES and type(None) in get_args(annotation)


def _nullable_model_paths(
    annotation: Any, field: FieldInfo, path: tuple[str, ...]
) -> Generator[tuple[str, ...]]:
    """Where a schema that may be None and has a default sits among flattened params.

    ``annotation`` is the schema a param or field of one is declared as,
    ``field`` what it was declared with and ``path`` where its fields are
    mapped to, the way ``_model_flatten_map`` maps them.
    """
    if _allows_none(annotation) and not field.is_required():
        yield path
    model = _unwrap_union_model(annotation)
    for attr, sub_field in model.model_fields.items():
        if is_pydantic_model(sub_field.annotation):
            yield from _nullable_model_paths(
                sub_field.annotation, sub_field, (*path, sub_field.alias or attr)
            )


def _unwrap_union_model(annotation: Any) -> Any:
    """If annotation is a Union containing a pydantic model, return that model class."""
    annotation = _unalias(annotation)
    if get_origin(annotation) in UNION_TYPES:
        for arg in get_args(annotation):
            if arg is not type(None) and is_pydantic_model(arg):
                return _unwrap_union_model(arg)
    return annotation


def is_pydantic_model(cls: Any) -> bool:
    cls = _unalias(cls)
    try:
        origin = get_origin(cls)

        # Handle Annotated types - extract the actual type
        if origin is Annotated:
            args = get_args(cls)
            return is_pydantic_model(args[0])

        # Handle Union types
        if origin in UNION_TYPES:
            return any(is_pydantic_model(arg) for arg in get_args(cls))
        return issubclass(cls, pydantic.BaseModel)
    except TypeError:  # pragma: no cover
        return False


def is_collection_type(annotation: Any) -> bool:
    annotation = _unalias(annotation)
    origin = get_origin(annotation)

    if origin in UNION_TYPES:
        for arg in get_args(annotation):
            if is_collection_type(arg):
                return True
        return False

    collection_types = (list, set, tuple)
    if origin is None:
        return (
            isinstance(annotation, collection_types)
            if not isinstance(annotation, type)
            else issubclass(annotation, collection_types)
        )
    else:
        return origin in collection_types  # TODO: I guess we should handle only list


_JSON_PRIMITIVE_TYPES = {"string", "integer", "number", "boolean", "null"}


def is_multipart_json_part(annotation: Any) -> bool:
    """Whether a Body param sent beside File/Form params is a JSON part.

    The spec documents it as a property of the multipart body, so it is read
    the way OpenAPI's default multipart encoding reads that property:
    primitives (str, int, date, Enum, Literal, UUID, ...) are plain text and
    everything else (schemas, lists, dicts, TypedDict, Any, ...) is JSON.
    """
    try:
        schema = pydantic.TypeAdapter(annotation).json_schema()
    except Exception:  # no JSON schema to go by: read it as JSON
        return True
    return not _is_primitive_schema(schema, schema.get("$defs", {}))


def _is_primitive_schema(schema: dict[str, Any], defs: dict[str, Any]) -> bool:
    if "$ref" in schema:
        return _is_primitive_schema(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            return all(_is_primitive_schema(s, defs) for s in schema[key])
    if "type" not in schema:
        # An untyped Literal[1, "a"] is primitive, {} (Any) is not.
        return "enum" in schema or "const" in schema
    types = schema["type"]
    return set(types if isinstance(types, list) else [types]) <= _JSON_PRIMITIVE_TYPES


def detect_collection_fields(
    args: list[FuncParam], flatten_map: dict[str, tuple[str, ...]]
) -> list[str]:
    """
    Django QueryDict has values that are always lists, so we need to help django-hattori to understand
    better the input parameters if it's a list or a single value
    This method detects attributes that should be treated by hattori as lists and returns this list as a result
    """
    result = [i.alias or i.name for i in args if i.is_collection]
    result.extend(
        name
        for name, field in _flattened_fields(args, flatten_map)
        if is_collection_type(field.annotation)
    )
    return result


def _flattened_fields(
    args: list[FuncParam], flatten_map: dict[str, tuple[str, ...]]
) -> Generator[tuple[str, FieldInfo]]:
    """The schema fields a flatten map reaches, by the parameter name each takes."""
    args_d = {arg.alias: arg for arg in args}
    for path in (p for p in flatten_map.values() if len(p) > 1):
        annotation_or_field: Any = args_d[path[0]].annotation
        for attr in path[1:]:
            if hasattr(annotation_or_field, "annotation"):
                annotation_or_field = annotation_or_field.annotation
            annotation_or_field = _unwrap_union_model(annotation_or_field)
            by_alias = _reads_by_alias(annotation_or_field)
            annotation_or_field = next(
                field
                for name, field in annotation_or_field.model_fields.items()
                if _read_name(field, name, by_alias) == attr
            )

            annotation_or_field = getattr(
                annotation_or_field, "outer_type_", annotation_or_field
            )

        yield path[-1], annotation_or_field
