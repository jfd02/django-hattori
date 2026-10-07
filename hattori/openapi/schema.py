import itertools
import re
from collections.abc import Generator, Sequence
from copy import copy, deepcopy
from http.client import responses as _stdlib_responses
from typing import TYPE_CHECKING, Any, get_args, get_origin

from pydantic.fields import FieldInfo
from pydantic.json_schema import JsonSchemaMode, models_json_schema
from pydantic_core import core_schema

from hattori.compatibility.util import UNION_TYPES
from hattori.errors import (
    ErrorBody,
    get_http_error_model,
    get_validation_error_model,
)
from hattori.operation import Operation
from hattori.params.models import TModels
from hattori.schema import HattoriGenerateJsonSchema
from hattori.utils import normalize_path

if TYPE_CHECKING:
    from hattori import HattoriAPI  # pragma: no cover
    from hattori.router import BoundRouter  # pragma: no cover

REF_TEMPLATE: str = "#/components/schemas/{model}"

# Override phrases updated in RFC 9110 so output is consistent across Python versions.
HTTP_STATUS_PHRASES = {**_stdlib_responses, 422: "Unprocessable Content"}

BODY_CONTENT_TYPES: dict[str, str] = {
    "body": "application/json",
    "form": "application/x-www-form-urlencoded",
    "file": "multipart/form-data",
}

type _SchemaField = (
    core_schema.ModelField
    | core_schema.DataclassField
    | core_schema.TypedDictField
    | core_schema.ComputedField
)


class ResponseJsonSchema(HattoriGenerateJsonSchema):
    """Apply an operation's omission rules to its response models only."""

    exclude_none = False
    exclude_defaults = False
    exclude_unset = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._core_definitions: dict[str, core_schema.CoreSchema] = {}

    def definitions_schema(
        self, schema: core_schema.DefinitionsSchema
    ) -> dict[str, Any]:
        self._core_definitions.update(
            (definition["ref"], definition) for definition in schema["definitions"]
        )
        return super().definitions_schema(schema)

    def _named_required_fields_schema(
        self, named_required_fields: Sequence[tuple[str, bool, _SchemaField]]
    ) -> dict[str, Any]:
        fields = []
        for name, required, field in named_required_fields:
            if self.mode == "serialization":
                value_schema = (
                    field["return_schema"]
                    if field["type"] == "computed-field"
                    else field["schema"]
                )
                if (
                    value_schema["type"] == "default"
                    and (self.exclude_defaults or self.exclude_unset)
                ) or (self.exclude_none and self._allows_none(value_schema)):
                    required = False
            fields.append((name, required, field))
        return super()._named_required_fields_schema(fields)

    def _allows_none(
        self, schema: core_schema.CoreSchema, seen_refs: frozenset[str] = frozenset()
    ) -> bool:
        # Exclusion happens before field serialization. Inspect the input core
        # schema, including named type aliases, rather than the serialized type.
        schema_type = schema["type"]
        if schema_type in ("any", "none", "nullable"):
            return True
        if schema_type == "function-plain":
            # Plain validators replace type validation entirely and may return
            # None even if a field serializer advertises a non-null result.
            return True
        if schema_type == "json":
            # Json[T] holds the decoded value at serialization time; bare Json
            # accepts any JSON value, including null.
            inner = schema.get("schema")
            return inner is None or self._allows_none(inner, seen_refs)
        if schema_type == "literal":
            return None in schema["expected"]
        if schema_type == "definition-ref":
            ref = schema["schema_ref"]
            definition = self._core_definitions.get(ref)
            return (
                definition is not None
                and ref not in seen_refs
                and self._allows_none(definition, seen_refs | {ref})
            )
        if schema_type == "union":
            return any(
                self._allows_none(
                    choice[0] if isinstance(choice, tuple) else choice, seen_refs
                )
                for choice in schema["choices"]
            )
        if schema_type in (
            "default",
            "function-before",
            "function-after",
            "function-wrap",
            "definitions",
        ):
            return self._allows_none(schema["schema"], seen_refs)
        return False


def get_schema(api: HattoriAPI, path_prefix: str = "") -> OpenAPISchema:
    openapi = OpenAPISchema(api, path_prefix)
    return openapi


def get_operation_id(
    api: HattoriAPI, operation: Operation, bound_router: BoundRouter, method: str
) -> str:
    """The ``operationId`` one method of an operation is documented under."""
    op_id = operation.operation_id or api.get_openapi_operation_id(
        operation, bound_router
    )
    extra = operation.openapi_extra or {}
    if "operationId" in extra:
        op_id = extra["operationId"]
    if len(operation.methods) > 1:
        op_id = f"{op_id}_{method.lower()}"
    return op_id


def _field_can_fail_validation(field: FieldInfo) -> bool:
    """Whether a value supplied for this field could fail validation.

    Deliberately conservative — it answers "is a 422 *impossible*", so anything
    it can't prove safe counts as fallible. Only a bare, unconstrained ``str``
    (optionally nullable) is provably safe: path/query/header/cookie values
    arrive as strings already, so there is nothing to coerce and no constraint
    to violate. A narrower type can fail to coerce, and ``metadata`` covers both
    constraints (``Field(min_length=...)``) and ``Annotated`` validators, whose
    rejections are invisible in the field's JSON Schema.
    """
    if field.metadata:
        return True
    annotation = field.annotation
    if get_origin(annotation) in UNION_TYPES:
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) != 1:
            return True
        annotation = args[0]
    return annotation is not str


def _params_can_fail_validation(model: Any) -> bool:
    """Whether any param on a non-body model could produce a 422."""
    decorators = getattr(model, "__pydantic_decorators__", None)
    if decorators is not None and (
        decorators.field_validators
        or decorators.model_validators
        or decorators.validators
    ):
        # A validator can reject anything; we can't reason about its body.
        return True

    source = model.__hattori_param_source__
    for field in model.model_fields.values():
        # A required param that isn't part of the path can simply be omitted.
        # Path params can't: the URL wouldn't have matched the route at all.
        if field.is_required() and source != "path":
            return True
        if _field_can_fail_validation(field):
            return True
    return False


class OpenAPISchema(dict):
    def __init__(self, api: HattoriAPI, path_prefix: str) -> None:
        self.api = api
        self.path_prefix = path_prefix
        self.schemas: dict[str, Any] = {}
        self.securitySchemes: dict[str, Any] = {}
        self._error_models = (get_validation_error_model(), get_http_error_model())
        # (path below the mount point, its methods) for every documented path
        self._routes: list[tuple[str, dict[str, Any]]] = []
        self._validation_error_title: str | None = None
        self._http_error_title: str | None = None
        # (final component name, serialized message field) -> declared message
        self._error_messages: dict[tuple[str, str], str] = {}
        extra_info = api.openapi_extra.get("info", {})
        super().__init__([
            ("openapi", "3.1.0"),
            (
                "info",
                {
                    "title": api.title,
                    "version": api.version,
                    "description": api.description,
                    **extra_info,
                },
            ),
            ("paths", self.get_paths()),
            ("components", self.get_components()),
            ("servers", api.servers),
        ])
        for k, v in api.openapi_extra.items():
            if k not in self:
                self[k] = v

    def get_paths(self) -> dict[str, Any]:
        routes = []
        # Use bound routers to ensure operations have correct auth/tags
        for bound_router in self.api._get_bound_routers():
            for path, path_view in bound_router.path_operations.items():
                path_methods = self.methods(path_view.operations, bound_router)
                if path_methods:
                    route = "/".join([i for i in (bound_router.prefix, path) if i])
                    routes.append((route, path_methods))
        self._routes = routes
        return self._prefixed_paths()

    def _prefixed_paths(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for route, path_methods in self._routes:
            full_path = "/" + self.path_prefix + route
            full_path = normalize_path(full_path)
            full_path = re.sub(r"{[^}:]+:", "{", full_path)  # remove path converters
            # Merged into a new dict: the methods are shared between prefixes.
            result[full_path] = {**result.get(full_path, {}), **path_methods}
        return result

    def is_current(self) -> bool:
        """Whether this still documents the error bodies that would be sent.

        The error models are process-wide and can be swapped at any time.
        """
        installed = (get_validation_error_model(), get_http_error_model())
        return self._error_models == installed

    def with_path_prefix(self, path_prefix: str) -> OpenAPISchema:
        """This document as it is served from another mount point.

        Only the keys of ``paths`` depend on where the API is mounted, so
        everything else is shared with this schema rather than built again.
        """
        if path_prefix == self.path_prefix:
            return self
        schema = copy(self)
        schema.path_prefix = path_prefix
        schema["paths"] = schema._prefixed_paths()
        return schema

    def methods(self, operations: list, bound_router: BoundRouter) -> dict[str, Any]:
        result = {}
        for op in operations:
            if op.include_in_schema:
                for method in op.methods:
                    result[method.lower()] = self.operation_details(
                        op, bound_router, method
                    )
        return result

    def deep_dict_update(
        self, main_dict: dict[Any, Any], update_dict: dict[Any, Any]
    ) -> None:
        for key in update_dict:
            if (
                key in main_dict
                and isinstance(main_dict[key], dict)
                and isinstance(update_dict[key], dict)
            ):
                self.deep_dict_update(main_dict[key], update_dict[key])
            elif (
                key in main_dict
                and isinstance(main_dict[key], list)
                and isinstance(update_dict[key], list)
            ):
                main_dict[key].extend(update_dict[key])
            else:
                main_dict[key] = update_dict[key]

    def operation_details(
        self, operation: Operation, bound_router: BoundRouter, method: str
    ) -> dict[str, Any]:
        op_id = get_operation_id(self.api, operation, bound_router, method)
        result: dict[str, Any] = {
            "operationId": op_id,
            "parameters": self.operation_parameters(operation),
            "responses": self.responses(operation),
        }

        if operation.summary:
            result["summary"] = operation.summary

        if operation.description:
            result["description"] = operation.description

        if operation.tags:
            result["tags"] = operation.tags

        if operation.deprecated:
            result["deprecated"] = operation.deprecated

        body = self.request_body(operation)
        if body:
            result["requestBody"] = body

        security = self.operation_security(operation)
        if security:
            result["security"] = security

        if operation.openapi_extra:
            extra = deepcopy(operation.openapi_extra)
            # Keep the existing Python API's integer response keys, while
            # accepting the string keys used in JSON OpenAPI documents.
            if isinstance(extra.get("responses"), dict):
                responses: dict[Any, Any] = {}
                for status, response in extra["responses"].items():
                    key = (
                        int(status)
                        if isinstance(status, str)
                        and re.fullmatch(r"[1-5][0-9]{2}", status)
                        else status
                    )
                    self.deep_dict_update(responses, {key: response})
                extra["responses"] = responses
            self.deep_dict_update(result, extra)
            result["operationId"] = op_id

        return result

    def operation_parameters(self, operation: Operation) -> list[dict[str, Any]]:
        result = []
        for model in operation.models:
            if model.__hattori_param_source__ not in BODY_CONTENT_TYPES:
                result.extend(self._extract_parameters(model))
        return result

    def _extract_parameters(self, model: Any) -> list[dict[str, Any]]:
        result = []
        csv_fields = set(getattr(model, "__hattori_csv_fields__", []))

        schema = model.model_json_schema(
            ref_template=REF_TEMPLATE,
            schema_generator=HattoriGenerateJsonSchema,
        )

        required = set(schema.get("required", []))
        properties = schema["properties"]

        for name, details in properties.items():
            is_required = name in required
            p_name: str
            p_schema: dict[str, Any]
            p_required: bool
            for p_name, p_schema, p_required in flatten_properties(
                name, details, is_required, schema.get("$defs", {})
            ):
                if not p_schema.get("include_in_schema", True):
                    continue

                param = {
                    "in": model.__hattori_param_source__,
                    "name": p_name,
                    "schema": p_schema,
                    "required": model.__hattori_param_source__ == "path" or p_required,
                }

                if p_name in csv_fields:
                    param["style"] = "form"
                    param["explode"] = False

                # copy description from schema description to param description
                if "description" in p_schema:
                    param["description"] = p_schema["description"]
                # A parameter's `examples` is a map of named Example Objects.
                # JSON Schema's own `examples` is a list of values, which is
                # valid only where it already is, on the schema.
                if isinstance(p_schema.get("examples"), dict):
                    param["examples"] = p_schema["examples"]
                    param["schema"] = {
                        key: value
                        for key, value in p_schema.items()
                        if key != "examples"
                    }
                elif "example" in p_schema:
                    param["example"] = p_schema["example"]
                if "deprecated" in p_schema:
                    param["deprecated"] = p_schema["deprecated"]

                result.append(param)

        # Extract first: named examples may live on fields of shared query
        # models, which must not retain the parameter-only map either.
        strip_named_examples(schema)
        if "$defs" in schema:
            renames = self.add_schema_definitions(schema["$defs"])
            for parameter in result:
                self.rename_schema_refs(parameter["schema"], renames)

        return result

    def _flatten_schema(self, model: Any) -> dict[str, Any]:
        params = self._extract_parameters(model)
        flattened = {
            "title": model.__name__,
            "type": "object",
            "properties": {p["name"]: p["schema"] for p in params},
        }
        required = [p["name"] for p in params if p["required"]]
        if required:
            flattened["required"] = required
        return flattened

    def _create_schema_from_model(
        self,
        model: Any,
        by_alias: bool = True,
        remove_level: bool = True,
        mode: JsonSchemaMode = "validation",
        ref_name_suffix: str = "",
        error_models: tuple[type[ErrorBody], ...] = (),
        schema_generator: type[HattoriGenerateJsonSchema] = HattoriGenerateJsonSchema,
    ) -> tuple[dict[str, Any], bool]:
        error_refs: dict[type[ErrorBody], str] = {}
        if hasattr(model, "__hattori_flatten_map__"):
            schema = self._flatten_schema(model)
        elif error_models:
            # Generate together to get Pydantic's actual refs, including its
            # qualified names when multiple models share a Python class name.
            refs, definitions = models_json_schema(
                [(model, mode), *((error, mode) for error in error_models)],
                ref_template=REF_TEMPLATE,
                by_alias=by_alias,
                schema_generator=schema_generator,
            )
            root_name = refs[(model, mode)]["$ref"].rsplit("/", 1)[-1]
            schema = definitions["$defs"].pop(root_name)
            schema["$defs"] = definitions["$defs"]
            error_refs = {
                error: refs[(error, mode)]["$ref"].rsplit("/", 1)[-1]
                for error in error_models
            }
        else:
            schema = model.model_json_schema(
                ref_template=REF_TEMPLATE,
                by_alias=by_alias,
                schema_generator=schema_generator,
                mode=mode,
            ).copy()

        # move Schemas from definitions
        if schema.get("$defs"):
            ref_renames = self.add_schema_definitions(
                schema.pop("$defs"), ref_name_suffix=ref_name_suffix
            )
            self.rename_schema_refs(schema, ref_renames)
            for body, name in error_refs.items():
                error = body.__hattori_error__
                message = getattr(error, "message", "")
                if message:
                    field = body.model_fields["message"]
                    field_name = (
                        field.serialization_alias or "message"
                        if by_alias
                        else "message"
                    )
                    key = (ref_renames.get(name, name), field_name)
                    self._error_messages.setdefault(key, message)

        if remove_level and len(schema["properties"]) == 1:
            name, details = list(schema["properties"].items())[0]

            # ref = details["$ref"]
            required = name in schema.get("required", {})
            return details, required
        else:
            return schema, bool(schema.get("required"))

    def _create_multipart_schema_from_models(
        self,
        models: TModels,
        mode: JsonSchemaMode = "validation",
    ) -> tuple[dict[str, Any], str]:
        # We have File and Form or Body, so we need to use multipart (File)
        content_type = BODY_CONTENT_TYPES["file"]

        # get the various schemas
        result = merge_schemas([
            self._create_schema_from_model(model, remove_level=False)[0]
            for model in models
        ])
        result["title"] = "MultiPartBodyParams"

        return result, content_type

    def request_body(self, operation: Operation) -> dict[str, Any]:
        models = [
            m
            for m in operation.models
            if m.__hattori_param_source__ in BODY_CONTENT_TYPES
        ]
        if not models:
            return {}

        if len(models) == 1:
            model = models[0]
            content_type = BODY_CONTENT_TYPES[model.__hattori_param_source__]
            schema, required = self._create_schema_from_model(
                model,
                remove_level=model.__hattori_param_source__ == "body",
                mode="validation",
            )
        else:
            schema, content_type = self._create_multipart_schema_from_models(
                models, mode="validation"
            )
            required = bool(schema.get("required"))

        return {
            "content": {content_type: {"schema": schema}},
            "required": required,
        }

    def responses(self, operation: Operation) -> dict[int, dict[str, Any]]:
        assert bool(operation.response_models), f"{operation.response_models} empty"

        generator = type(
            "OperationResponseJsonSchema",
            (ResponseJsonSchema,),
            {
                "exclude_none": operation.exclude_none,
                "exclude_defaults": operation.exclude_defaults,
                "exclude_unset": operation.exclude_unset,
            },
        )

        result = {}
        for status, model in operation.response_models.items():
            if status == Ellipsis:
                continue  # it's not yet clear what it means if user wants to output any other code

            description = HTTP_STATUS_PHRASES.get(status, "Unknown Status Code")
            details: dict[int, Any] = {status: {"description": description}}
            if model is not None:
                ref_name_suffix = "_by_alias" if operation.by_alias else ""
                schema = self._create_schema_from_model(
                    model,
                    by_alias=operation.by_alias,
                    mode="serialization",
                    ref_name_suffix=ref_name_suffix,
                    schema_generator=generator,
                    error_models=tuple(
                        self._error_body_models(
                            model.model_fields["response"].annotation
                        )
                    ),
                )[0]
                self._prefer_one_of_for_const_property_union(schema, "code")
                # Only the streamed body carries the stream media type. Other
                # responses on a streaming op (auth/permission short-circuits,
                # extra declared errors) are dispatched as ordinary JSON at
                # runtime, so document them with the renderer's media type.
                if (
                    operation.stream_format is not None
                    and model is operation.stream_item_model
                ):
                    details[status]["content"] = (
                        operation.stream_format.openapi_content_schema(schema)
                    )
                else:
                    details[status]["content"] = {
                        self.api.renderer.media_type: {"schema": schema}
                    }
            result.update(details)

        if any(m.__hattori_param_source__ == "body" for m in operation.models):
            # JSON decoding fails before Pydantic validation. Preserve explicitly
            # declared 400 responses alongside the framework's HttpError body.
            http_error_schema = {
                "$ref": REF_TEMPLATE.format(model=self._get_http_error_title())
            }
            self._add_response_schema(result, 400, http_error_schema)

        if operation.models and self._can_fail_validation(operation):
            validation_schema = {
                "$ref": REF_TEMPLATE.format(model=self._get_validation_error_title())
            }
            self._add_response_schema(result, 422, validation_schema)

        return result

    def _add_response_schema(self, result: dict, status: int, schema: dict) -> None:
        response = result.setdefault(
            status, {"description": HTTP_STATUS_PHRASES[status]}
        )
        media = response.setdefault("content", {}).setdefault(
            self.api.renderer.media_type, {}
        )
        existing = media.get("schema")
        # Keep discriminators local to explicitly declared error unions.
        media["schema"] = (
            schema
            if existing is None or existing == schema
            else {"anyOf": [existing, schema]}
        )

    def _can_fail_validation(self, operation: Operation) -> bool:
        """Whether this operation can actually return a 422.

        An operation with only unconstrained ``str`` path params has nothing
        that can fail to validate, so documenting a 422 on it would promise a
        response the route can never send.
        """
        return any(
            model.__hattori_param_source__ in BODY_CONTENT_TYPES
            or _params_can_fail_validation(model)
            for model in operation.models
        )

    def _get_validation_error_title(self) -> str:
        title = self._validation_error_title
        if title is None:
            title = self._register_error_model(get_validation_error_model())
            self._validation_error_title = title
        return title

    def _get_http_error_title(self) -> str:
        title = self._http_error_title
        if title is None:
            title = self._register_error_model(get_http_error_model())
            self._http_error_title = title
        return title

    def _register_error_model(self, model: Any) -> str:
        schema = self._create_schema_from_model(model, remove_level=False)[0]
        base_title = schema.get("title", model.__name__)
        # Register through the collision-aware path (rather than writing
        # self.schemas[title] directly) so a user model that happens to share
        # the body model's name is never clobbered by — and never clobbers —
        # the framework's auto-generated schema.
        renames = self.add_schema_definitions({base_title: schema})
        title: str = renames.get(base_title, base_title)
        return title

    def operation_security(self, operation: Operation) -> list[dict[str, Any]] | None:
        if not operation.auth_callbacks:
            return None
        result = []
        for auth in operation.auth_callbacks:
            security_schema = getattr(auth, "openapi_security_schema", None)
            if security_schema is not None:
                scopes: list[dict[str, Any]] = []  # TODO: scopes
                name = self._unique_security_scheme_name(
                    auth.__class__.__name__, security_schema
                )
                result.append({name: scopes})
                self.securitySchemes[name] = security_schema
        return result

    def _unique_security_scheme_name(self, name: str, schema: dict[str, Any]) -> str:
        existing = self.securitySchemes.get(name)
        if existing is None or existing == schema:
            return name
        index = 2
        candidate = f"{name}_{index}"
        while (
            candidate in self.securitySchemes
            and self.securitySchemes[candidate] != schema
        ):
            index += 1
            candidate = f"{name}_{index}"
        return candidate

    def _error_body_models(self, annotation: Any) -> Generator[type[ErrorBody]]:
        if isinstance(annotation, type) and issubclass(annotation, ErrorBody):
            if annotation.__hattori_error__ is not None:
                yield annotation
        for arg in get_args(annotation):
            yield from self._error_body_models(arg)

    def _document_error_messages(self) -> None:
        """Show each error's declared message as the example of its ``message``.

        Applied once every schema is registered. An example inside the body
        model itself would make two same-named errors that differ only in
        wording into different schemas, and one of them would be renamed.
        """
        for (name, field_name), message in self._error_messages.items():
            message_schema = self.schemas[name].get("properties", {}).get(field_name)
            if isinstance(message_schema, dict):
                message_schema.setdefault("examples", [message])

    def get_components(self) -> dict[str, Any]:
        self._document_error_messages()
        result = {"schemas": self.schemas}
        if self.securitySchemes:
            result["securitySchemes"] = self.securitySchemes
        return result

    def _prefer_one_of_for_const_property_union(
        self, schema: dict[str, Any], property_name: str
    ) -> None:
        """Rewrite unambiguous response unions from anyOf to oneOf.

        Pydantic emits plain ``anyOf`` for Python unions. For error responses
        where every branch has a unique constant ``code`` value, those branches
        are mutually exclusive and OpenAPI should expose that stronger contract.
        """
        variants = schema.get("anyOf")
        if not isinstance(variants, list) or "oneOf" in schema:
            return

        mapping: dict[str, str] = {}
        for variant in variants:
            if not isinstance(variant, dict):
                return
            ref = variant.get("$ref")
            if not isinstance(ref, str):
                return
            name = ref.rsplit("/", 1)[-1]
            value = self._schema_const_property_value(
                self.schemas.get(name), property_name
            )
            if value is None or value in mapping:
                return
            mapping[value] = ref

        schema["oneOf"] = schema.pop("anyOf")
        schema["discriminator"] = {
            "propertyName": property_name,
            "mapping": mapping,
        }

    def _schema_const_property_value(
        self, schema: Any, property_name: str
    ) -> str | None:
        # Strings only: OpenAPI ``discriminator.mapping`` keys must be strings,
        # so non-string consts/enums (e.g. integer codes) deliberately return
        # None and the caller falls back to plain ``anyOf``.
        if not isinstance(schema, dict):
            return None
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return None
        prop_schema = properties.get(property_name)
        if not isinstance(prop_schema, dict):
            return None

        const_value = prop_schema.get("const")
        if isinstance(const_value, str):
            return const_value

        enum_value = prop_schema.get("enum")
        if (
            isinstance(enum_value, list)
            and len(enum_value) == 1
            and isinstance(enum_value[0], str)
        ):
            return enum_value[0]

        return None

    def rename_schema_refs(self, value: Any, ref_renames: dict[str, str]) -> None:
        refs = {
            REF_TEMPLATE.format(model=old): REF_TEMPLATE.format(model=new)
            for old, new in ref_renames.items()
        }
        for schema in schema_nodes(value):
            for keyword in ("$ref", "$dynamicRef"):
                ref = schema.get(keyword)
                if isinstance(ref, str) and ref in refs:
                    schema[keyword] = refs[ref]
            mapping = schema.get("discriminator", {}).get("mapping", {})
            for tag, ref in mapping.items():
                if ref in refs:
                    mapping[tag] = refs[ref]

    def add_schema_definitions(
        self, definitions: dict[str, Any], ref_name_suffix: str = ""
    ) -> dict[str, str]:
        # Iterate to a fixed point so renames cascade through cross-references:
        # if def B is renamed because it differs from an existing B, any incoming
        # def A that references B must also be rewritten — and that rewrite can
        # in turn cause A to differ from the existing A and need its own rename.
        ref_renames: dict[str, str] = {}
        while True:
            # Always rewrite the originals, so a previous replacement can
            # never be mistaken for another original component name.
            incoming = deepcopy(definitions)
            for schema in incoming.values():
                self.rename_schema_refs(schema, ref_renames)

            new_renames = False
            for name, schema in incoming.items():
                existing = self.schemas.get(ref_renames.get(name, name))
                if existing is None or existing == schema:
                    continue
                ref_renames[name] = self._unique_schema_name(
                    name,
                    ref_name_suffix,
                    schema,
                    reserved_names=set(definitions) | set(ref_renames.values()),
                )
                new_renames = True

            if not new_renames:
                break

        for name, schema in incoming.items():
            final_name = ref_renames[name] if name in ref_renames else name
            self.schemas[final_name] = schema

        return ref_renames

    def _unique_schema_name(
        self, name: str, suffix: str, schema: dict[str, Any], reserved_names: set[str]
    ) -> str:
        candidate = f"{name}{suffix}" if suffix else f"{name}_2"
        index = 2
        while candidate in reserved_names or (
            candidate in self.schemas and self.schemas[candidate] != schema
        ):
            index += 1
            candidate = f"{name}{suffix}_{index}" if suffix else f"{name}_{index}"
        return candidate


def schema_nodes(schema: Any) -> Generator[dict[str, Any]]:
    """Walk schemas, treating examples, defaults and extension values as data."""
    if not isinstance(schema, dict):
        return
    yield schema
    for keyword in ("properties", "patternProperties", "$defs", "dependentSchemas"):
        for child in schema.get(keyword, {}).values():
            yield from schema_nodes(child)
    for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        for child in schema.get(keyword, []):
            yield from schema_nodes(child)
    for keyword in (
        "items",
        "additionalProperties",
        "unevaluatedProperties",
        "unevaluatedItems",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
    ):
        child = schema.get(keyword)
        yield from schema_nodes(child)


def strip_named_examples(schema: dict[str, Any]) -> None:
    """Remove parameter example maps from schemas, without traversing payloads."""
    for node in schema_nodes(schema):
        if isinstance(node.get("examples"), dict):
            del node["examples"]


def flatten_properties(
    prop_name: str,
    prop_details: dict[str, Any],
    prop_required: bool,
    definitions: dict[str, Any],
) -> Generator[tuple[str, dict[str, Any], bool]]:
    """
    extracts all nested model's properties into flat properties
    (used f.e. in GET params with multiple arguments and models)
    """
    if "anyOf" in prop_details:
        variants = prop_details["anyOf"]
        non_null = [variant for variant in variants if variant.get("type") != "null"]
        if len(variants) == 2 and len(non_null) == 1:
            variant = non_null[0]
            definition = variant
            if "$ref" in variant:
                definition = definitions[variant["$ref"].rsplit("/", 1)[-1]]
            if "properties" in definition:
                # Runtime flattens nullable query models just like required
                # models. Keep nullable scalar/enum/list schemas intact.
                yield from flatten_properties(
                    prop_name, definition, prop_required, definitions
                )
                return
        yield prop_name, prop_details, prop_required

    elif "allOf" in prop_details:
        resolve_allOf(prop_details, definitions)
        if len(prop_details["allOf"]) == 1 and "enum" in prop_details["allOf"][0]:
            # is_required = "default" not in prop_details
            yield prop_name, prop_details, prop_required
        else:
            # Nested model fields with a default value wrap in
            # ``allOf: [{$ref: ...}]`` via HattoriGenerateJsonSchema.default_schema
            # (so ``default`` can sit alongside the ref). After resolve_allOf
            # inlines the referent, we project its properties out.
            for item in prop_details["allOf"]:
                yield from flatten_properties("", item, True, definitions)

    elif "items" in prop_details and "$ref" in prop_details["items"]:
        def_name = prop_details["items"]["$ref"].rsplit("/", 1)[-1]
        prop_details["items"].update(definitions[def_name])
        del prop_details["items"]["$ref"]  # seems num data is there so ref not needed
        yield prop_name, prop_details, prop_required

    elif "$ref" in prop_details:
        def_name = prop_details["$ref"].split("/")[-1]
        definition = definitions[def_name]
        siblings = {k: v for k, v in prop_details.items() if k != "$ref"}
        if siblings and "properties" not in definition:
            # An enum (or other non-model) field: what the field declares beside
            # the reference describes this parameter, not the shared definition.
            definition = {**definition, **siblings}
        yield from flatten_properties(prop_name, definition, prop_required, definitions)

    elif "properties" in prop_details:
        required = set(prop_details.get("required", []))
        for k, v in prop_details["properties"].items():
            is_required = k in required
            yield from flatten_properties(k, v, is_required, definitions)
    else:
        yield prop_name, prop_details, prop_required


def resolve_allOf(details: dict[str, Any], definitions: dict[str, Any]) -> None:
    """
    resolves all $ref's in 'allOf' section
    """
    for item in details["allOf"]:
        if "$ref" in item:
            def_name = item["$ref"].rsplit("/", 1)[-1]
            item.update(definitions[def_name])
            del item["$ref"]


def merge_schemas(schemas: list[dict[str, Any]]) -> dict[str, Any]:
    result = schemas[0]
    for scm in schemas[1:]:
        result["properties"].update(scm["properties"])

    required_list = result.get("required", [])
    required_list.extend(
        itertools.chain.from_iterable(
            schema.get("required", ()) for schema in schemas[1:]
        )
    )
    if required_list:
        result["required"] = required_list
    return result
