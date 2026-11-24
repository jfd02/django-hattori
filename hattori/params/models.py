import re
from abc import ABC, abstractmethod
from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
    cast,
)

from django.conf import settings
from django.http import HttpRequest
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from hattori.errors import HttpError
from hattori.responses import json_loads

if TYPE_CHECKING:
    from hattori import HattoriAPI  # pragma: no cover

__all__ = [
    "ParamModel",
    "QueryModel",
    "PathModel",
    "HeaderModel",
    "CookieModel",
    "BodyModel",
    "FormModel",
    "FileModel",
]

TModel = TypeVar("TModel", bound="ParamModel")
TModels = list[TModel]


class ParamModel(BaseModel, ABC):
    __hattori_param_source__ = None

    @classmethod
    @abstractmethod
    def get_request_data(
        cls, request: HttpRequest, api: HattoriAPI, path_params: dict[str, Any]
    ) -> dict[str, Any]:
        pass  # pragma: no cover

    @classmethod
    def resolve(
        cls: type[TModel],
        request: HttpRequest,
        api: HattoriAPI,
        path_params: dict[str, Any],
    ) -> TModel:
        data = cls.get_request_data(request, api, path_params)
        data = cls._map_data_paths(data)
        return cls.model_validate(data, context={"request": request})

    @classmethod
    def _map_data_paths(cls, data: dict[str, Any]) -> dict[str, Any]:
        flatten_map = getattr(cls, "__hattori_flatten_map__", None)
        if not flatten_map:
            return data

        mapped_data: dict[str, Any] = {}
        sent: set[tuple[str, ...]] = set()
        for key, path in flatten_map.items():
            value = data.get(key)
            if value is not None:
                sent.update(path[:depth] for depth in range(1, len(path)))
            cls._map_data_path(mapped_data, value, path)
        # A nullable schema with a default, none of whose fields was sent,
        # takes that default instead of being built from no data at all.
        for path in getattr(cls, "__hattori_nullable_models__", ()):
            if path not in sent:
                _pop_path(mapped_data, path)
        return mapped_data

    @classmethod
    def _map_data_path(
        cls, data: dict[str, Any], value: Any, path: tuple[str, ...]
    ) -> None:
        current = data
        for key in path[:-1]:
            current = current.setdefault(key, {})
        if value is not None:
            current[path[-1]] = value


def _pop_path(data: dict[str, Any], path: tuple[str, ...]) -> None:
    *parents, last = path
    for key in parents:
        data = data.get(key)  # type: ignore[assignment]
        if not isinstance(data, dict):
            return
    data.pop(last, None)


def _parse_querydict(
    data: Any,
    list_fields: list[str],
    csv_fields: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in data.keys():
        if key in list_fields:
            values = data.getlist(key)
            if csv_fields and key in csv_fields:
                values = [item for v in values for item in v.split(",") if item]
            result[key] = values
        else:
            result[key] = data[key]
    return result


def _split_lists(data: Any, list_fields: list[str]) -> dict[str, Any]:
    """``data`` with each of ``list_fields`` split on its commas.

    A list in a path, a header or a cookie is one comma-separated value, which
    is how OpenAPI writes an array there.
    """
    result = dict(data)
    for name in list_fields:
        if isinstance(result.get(name), str):
            items = (item.strip() for item in result[name].split(","))
            result[name] = [item for item in items if item]
    return result


class QueryModel(ParamModel):
    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: HattoriAPI, path_params: dict[str, Any]
    ) -> dict[str, Any]:
        list_fields = getattr(cls, "__hattori_collection_fields__", [])
        csv_fields = getattr(cls, "__hattori_csv_fields__", None)
        return _parse_querydict(request.GET, list_fields, csv_fields)


class PathModel(ParamModel):
    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: HattoriAPI, path_params: dict[str, Any]
    ) -> dict[str, Any]:
        list_fields = getattr(cls, "__hattori_collection_fields__", [])
        return _split_lists(path_params, list_fields)


class HeaderModel(ParamModel):
    __hattori_flatten_map__: dict[str, Any]

    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: HattoriAPI, path_params: dict[str, Any]
    ) -> dict[str, Any]:
        data = {}
        headers = request.headers
        for name in cls.__hattori_flatten_map__:
            if name in headers:
                data[name] = headers[name]
        list_fields = getattr(cls, "__hattori_collection_fields__", [])
        return _split_lists(data, list_fields)


class CookieModel(ParamModel):
    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: HattoriAPI, path_params: dict[str, Any]
    ) -> dict[str, Any]:
        list_fields = getattr(cls, "__hattori_collection_fields__", [])
        return _split_lists(request.COOKIES, list_fields)


def is_json_media_type(content_type: str) -> bool:
    """Whether ``content_type`` (lowercase, without parameters) is a JSON one."""
    return content_type == "application/json" or (
        content_type.startswith("application/") and content_type.endswith("+json")
    )


class BodyModel(ParamModel):
    __read_from_single_attr__: str

    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: HattoriAPI, path_params: dict[str, Any]
    ) -> dict[str, Any]:
        if request.body:
            # A browser sends a form across sites as text/plain, as form data
            # or as nothing at all, cookies included, without asking first. A
            # JSON type it has to ask for, so a body is only read as JSON when
            # it says it is.
            if not is_json_media_type(request.content_type or ""):
                raise HttpError(
                    415, "Unsupported Media Type: send the body as application/json"
                )
            return cls._decode(request.body)

        # No body sends none of the params, which is not the same as a JSON
        # null: that is a value, which a body of several params cannot be.
        return {}

    @classmethod
    def _decode(cls, body: bytes | str) -> dict[str, Any]:
        try:
            data = json_loads(body)
        except Exception as e:
            msg = "Cannot parse request body"
            if settings.DEBUG:
                msg += f" ({e})"
            raise HttpError(400, msg) from e

        varname = getattr(cls, "__read_from_single_attr__", None)
        if varname:
            data = {varname: data}
        return cast("dict[str, Any]", data)


class FormModel(ParamModel):
    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: HattoriAPI, path_params: dict[str, Any]
    ) -> dict[str, Any]:
        list_fields = getattr(cls, "__hattori_collection_fields__", [])
        return _parse_querydict(request.POST, list_fields)


class FileModel(ParamModel):
    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: HattoriAPI, path_params: dict[str, Any]
    ) -> dict[str, Any]:
        list_fields = getattr(cls, "__hattori_collection_fields__", [])
        return _parse_querydict(request.FILES, list_fields)


class _MultiPartBodyModel(BodyModel):
    # How each body param's part is read ("json", "text" or "scalar"), by alias.
    __hattori_body_params__: dict[str, str]

    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: HattoriAPI, path_params: dict[str, Any]
    ) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for name, kind in cls.__hattori_body_params__.items():
            if name in request.POST:
                data = request.POST[name]
                if kind == "text":
                    # A primitive that can be a string (str, date, Enum, UUID,
                    # ...) is sent as its plain text value, as the spec's
                    # default multipart encoding has it, and validated as it
                    # is, like a Form field.
                    results[name] = data
                elif kind == "scalar":
                    results[name] = _read_scalar(data)
                else:
                    # A form field, read as JSON by declaration: the request's
                    # media type is the form's, so there is none to hold it to.
                    results[name] = cls._decode(data.encode()) if data else None
        return results


def _read_scalar(text: str) -> Any:
    """The number, boolean or null ``text`` spells, else ``text`` itself.

    A primitive that cannot be a string (int, bool, Literal[1, 2], StrictInt,
    ...) is sent as text too, but its validator would not take it as a string.
    """
    try:
        value = json_loads(text)
    except Exception:
        return text
    return value if value is None or isinstance(value, (bool, int, float)) else text


class CommaSeparated:
    """Field metadata marking a list as one comma-separated value (``explode=False``).

    ``Query(explode=False)`` leaves this in the field's metadata because that is
    what survives when the declaration sits on a field of a schema: pydantic
    rebuilds such a field as a plain ``FieldInfo``, dropping ``Param``'s own
    attributes but keeping its metadata.
    """


def is_comma_separated(field: FieldInfo) -> bool:
    return any(isinstance(item, CommaSeparated) for item in field.metadata)


class Param(FieldInfo):  # type: ignore[misc]
    def __init__(
        self,
        default: Any,
        *,
        alias: str | None = None,
        title: str | None = None,
        description: str | None = None,
        gt: float | None = None,
        ge: float | None = None,
        lt: float | None = None,
        le: float | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
        example: Any | None = None,
        examples: dict[str, Any] | None = None,
        deprecated: bool | None = None,
        include_in_schema: bool | None = True,
        pattern: str | re.Pattern[str] | None = None,
        explode: bool = True,
        # param_name: str = None,
        # param_type: Any = None,
        **extra: Any,
    ):
        self.deprecated = deprecated
        self.explode = explode
        # self.param_name: str = None
        # self.param_type: Any = None
        self.model_field: FieldInfo | None = None
        json_schema_extra = {}
        if example is not None:
            json_schema_extra["example"] = example
        if examples is not None:
            json_schema_extra["examples"] = examples
        if deprecated:
            json_schema_extra["deprecated"] = deprecated
        if not include_in_schema:
            json_schema_extra["include_in_schema"] = include_in_schema
        if alias and not extra.get("validation_alias"):
            extra["validation_alias"] = alias
        if alias and not extra.get("serialization_alias"):
            extra["serialization_alias"] = alias

        super().__init__(
            default=default,
            alias=alias,
            title=title,
            description=description,
            gt=gt,
            ge=ge,
            lt=lt,
            le=le,
            min_length=min_length,
            max_length=max_length,
            pattern=pattern,
            json_schema_extra=json_schema_extra,
            **extra,
        )
        if not explode:
            self.metadata.append(CommaSeparated())

    @classmethod
    def _param_source(cls) -> str:
        "Openapi param.in value or body type"
        return cls.__name__.lower()


class Path(Param):  # type: ignore[misc]
    _model = PathModel


class Query(Param):  # type: ignore[misc]
    _model = QueryModel


class Header(Param):  # type: ignore[misc]
    _model = HeaderModel


class Cookie(Param):  # type: ignore[misc]
    _model = CookieModel


class Body(Param):  # type: ignore[misc]
    _model = BodyModel


class Form(Param):  # type: ignore[misc]
    _model = FormModel


class File(Param):  # type: ignore[misc]
    _model = FileModel


class _MultiPartBody(Param):  # type: ignore[misc]
    _model = _MultiPartBodyModel

    @classmethod
    def _param_source(cls) -> str:
        return "body"
