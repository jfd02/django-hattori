import re
from abc import ABC, abstractmethod
from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
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
        cls, request: HttpRequest, api: "HattoriAPI", path_params: dict[str, Any]
    ) -> dict[str, Any] | None:
        pass  # pragma: no cover

    @classmethod
    def resolve(
        cls: type[TModel],
        request: HttpRequest,
        api: "HattoriAPI",
        path_params: dict[str, Any],
    ) -> TModel:
        data = cls.get_request_data(request, api, path_params)
        if data is None:
            return cls()

        data = cls._map_data_paths(data)
        return cls.model_validate(data, context={"request": request})

    @classmethod
    def _map_data_paths(cls, data: dict[str, Any]) -> dict[str, Any]:
        flatten_map = getattr(cls, "__hattori_flatten_map__", None)
        if not flatten_map:
            return data

        mapped_data: dict[str, Any] = {}
        for key, path in flatten_map.items():
            cls._map_data_path(mapped_data, data.get(key), path)
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


class QueryModel(ParamModel):
    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: "HattoriAPI", path_params: dict[str, Any]
    ) -> dict[str, Any] | None:
        list_fields = getattr(cls, "__hattori_collection_fields__", [])
        csv_fields = getattr(cls, "__hattori_csv_fields__", None)
        return _parse_querydict(request.GET, list_fields, csv_fields)


class PathModel(ParamModel):
    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: "HattoriAPI", path_params: dict[str, Any]
    ) -> dict[str, Any] | None:
        return path_params


class HeaderModel(ParamModel):
    __hattori_flatten_map__: dict[str, Any]

    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: "HattoriAPI", path_params: dict[str, Any]
    ) -> dict[str, Any] | None:
        data = {}
        headers = request.headers
        for name in cls.__hattori_flatten_map__:
            if name in headers:
                data[name] = headers[name]
        return data


class CookieModel(ParamModel):
    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: "HattoriAPI", path_params: dict[str, Any]
    ) -> dict[str, Any] | None:
        return request.COOKIES


class BodyModel(ParamModel):
    __read_from_single_attr__: str

    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: "HattoriAPI", path_params: dict[str, Any]
    ) -> dict[str, Any] | None:
        if request.body:
            try:
                data = json_loads(request.body)
            except Exception as e:
                msg = "Cannot parse request body"
                if settings.DEBUG:
                    msg += f" ({e})"
                raise HttpError(400, msg) from e

            varname = getattr(cls, "__read_from_single_attr__", None)
            if varname:
                data = {varname: data}
            return data

        return None


class FormModel(ParamModel):
    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: "HattoriAPI", path_params: dict[str, Any]
    ) -> dict[str, Any] | None:
        list_fields = getattr(cls, "__hattori_collection_fields__", [])
        return _parse_querydict(request.POST, list_fields)


class FileModel(ParamModel):
    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: "HattoriAPI", path_params: dict[str, Any]
    ) -> dict[str, Any] | None:
        list_fields = getattr(cls, "__hattori_collection_fields__", [])
        return _parse_querydict(request.FILES, list_fields)


class _HttpRequest(HttpRequest):
    body: bytes = b""


class _MultiPartBodyModel(BodyModel):
    __hattori_body_params__: dict[str, Any]

    @classmethod
    def get_request_data(
        cls, request: HttpRequest, api: "HattoriAPI", path_params: dict[str, Any]
    ) -> dict[str, Any] | None:
        req = _HttpRequest()
        get_request_data = super().get_request_data
        results: dict[str, Any] = {}
        for name, annotation in cls.__hattori_body_params__.items():
            if name in request.POST:
                data = request.POST[name]
                if annotation is str and (
                    not data or (data[0] != '"' and data[-1] != '"')
                ):
                    data = f'"{data}"'
                req.body = data.encode()
                results[name] = get_request_data(req, api, path_params)
        return results


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
