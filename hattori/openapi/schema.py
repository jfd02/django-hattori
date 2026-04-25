import itertools
import re
from collections.abc import Generator
from http.client import responses as _stdlib_responses
from typing import TYPE_CHECKING, Any

from pydantic.json_schema import JsonSchemaMode

from hattori.errors import ConfigError, ValidationErrorResponse
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


def get_schema(api: "HattoriAPI", path_prefix: str = "") -> "OpenAPISchema":
    openapi = OpenAPISchema(api, path_prefix)
    return openapi


class OpenAPISchema(dict):
    def __init__(self, api: "HattoriAPI", path_prefix: str) -> None:
        self.api = api
        self.path_prefix = path_prefix
        self.schemas: dict[str, Any] = {}
        self.securitySchemes: dict[str, Any] = {}
        self.all_operation_ids: set = set()
        self._validation_error_title: str | None = None
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
        result: dict[str, Any] = {}
        # Use bound routers to ensure operations have correct auth/tags
        for bound_router in self.api._get_bound_routers():
            for path, path_view in bound_router.path_operations.items():
                full_path = "/".join([i for i in (bound_router.prefix, path) if i])
                full_path = "/" + self.path_prefix + full_path
                full_path = normalize_path(full_path)
                full_path = re.sub(
                    r"{[^}:]+:", "{", full_path
                )  # remove path converters
                path_methods = self.methods(path_view.operations, bound_router)
                if path_methods:
                    try:
                        result[full_path].update(path_methods)
                    except KeyError:
                        result[full_path] = path_methods

        return result

    def methods(
        self, operations: list, bound_router: "BoundRouter"
    ) -> dict[str, Any]:
        result = {}
        for op in operations:
            if op.include_in_schema:
                operation_details = self.operation_details(op, bound_router)
                for method in op.methods:
                    result[method.lower()] = operation_details
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
        self, operation: Operation, bound_router: "BoundRouter"
    ) -> dict[str, Any]:
        op_id = operation.operation_id or self.api.get_openapi_operation_id(
            operation, bound_router
        )
        if op_id in self.all_operation_ids:
            raise ConfigError(
                f'Duplicate operation_id "{op_id}" '
                f"(at {operation.view_func.__module__}.{operation.view_func.__name__}). "
                "Pass an explicit operation_id= or rename the view."
            )
        self.all_operation_ids.add(op_id)
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
            result["deprecated"] = operation.deprecated  # type: ignore

        body = self.request_body(operation)
        if body:
            result["requestBody"] = body

        security = self.operation_security(operation)
        if security:
            result["security"] = security

        if operation.openapi_extra:
            self.deep_dict_update(result, operation.openapi_extra)

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

        if "$defs" in schema:
            self.add_schema_definitions(schema["$defs"])

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
                    "required": p_required,
                }

                if p_name in csv_fields:
                    param["style"] = "form"
                    param["explode"] = False

                # copy description from schema description to param description
                if "description" in p_schema:
                    param["description"] = p_schema["description"]
                if "examples" in p_schema:
                    param["examples"] = p_schema["examples"]
                elif "example" in p_schema:
                    param["example"] = p_schema["example"]
                if "deprecated" in p_schema:
                    param["deprecated"] = p_schema["deprecated"]

                result.append(param)

        return result

    def _flatten_schema(self, model: Any) -> dict[str, Any]:
        params = self._extract_parameters(model)
        flattened = {
            "title": model.__name__,  # type: ignore
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
    ) -> tuple[dict[str, Any], bool]:
        if hasattr(model, "__hattori_flatten_map__"):
            schema = self._flatten_schema(model)
        else:
            schema = model.model_json_schema(
                ref_template=REF_TEMPLATE,
                by_alias=by_alias,
                schema_generator=HattoriGenerateJsonSchema,
                mode=mode,
            ).copy()

        # move Schemas from definitions
        if schema.get("$defs"):
            ref_renames = self.add_schema_definitions(
                schema.pop("$defs"), ref_name_suffix=ref_name_suffix
            )
            self.rename_schema_refs(schema, ref_renames)

        if remove_level and len(schema["properties"]) == 1:
            name, details = list(schema["properties"].items())[0]

            # ref = details["$ref"]
            required = name in schema.get("required", {})
            return details, required
        else:
            return schema, True

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
            required = True

        return {
            "content": {content_type: {"schema": schema}},
            "required": required,
        }

    def responses(self, operation: Operation) -> dict[int, dict[str, Any]]:
        assert bool(operation.response_models), f"{operation.response_models} empty"

        result = {}
        for status, model in operation.response_models.items():
            if status == Ellipsis:
                continue  # it's not yet clear what it means if user wants to output any other code

            description = HTTP_STATUS_PHRASES.get(status, "Unknown Status Code")
            details: dict[int, Any] = {status: {"description": description}}
            if model is not None:
                # ::TODO:: test this: by_alias == True
                ref_name_suffix = "_by_alias" if operation.by_alias else ""
                schema = self._create_schema_from_model(
                    model,
                    by_alias=operation.by_alias,
                    mode="serialization",
                    ref_name_suffix=ref_name_suffix,
                )[0]
                self._prefer_one_of_for_const_property_union(schema, "code")
                if operation.stream_format is not None:
                    details[status]["content"] = (
                        operation.stream_format.openapi_content_schema(schema)
                    )
                else:
                    details[status]["content"] = {
                        self.api.renderer.media_type: {"schema": schema}
                    }
            result.update(details)

        if operation.models and 422 not in result:
            result[422] = {
                "description": HTTP_STATUS_PHRASES.get(422, "Unknown Status Code"),
                "content": {
                    self.api.renderer.media_type: {
                        "schema": {
                            "$ref": REF_TEMPLATE.format(
                                model=self._get_validation_error_title()
                            )
                        }
                    }
                },
            }

        return result

    def _get_validation_error_title(self) -> str:
        title = self._validation_error_title
        if title is None:
            schema = self._create_schema_from_model(
                ValidationErrorResponse, remove_level=False
            )[0]
            title = schema.get("title", "ValidationErrorResponse")
            self.schemas[title] = schema
            self._validation_error_title = title
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

    def _unique_security_scheme_name(
        self, name: str, schema: dict[str, Any]
    ) -> str:
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

    def get_components(self) -> dict[str, Any]:
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
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str):
                name = ref.rsplit("/", 1)[-1]
                if name in ref_renames:
                    value["$ref"] = REF_TEMPLATE.format(model=ref_renames[name])
            for item in value.values():
                self.rename_schema_refs(item, ref_renames)
        elif isinstance(value, list):
            for item in value:
                self.rename_schema_refs(item, ref_renames)

    def add_schema_definitions(
        self, definitions: dict[str, Any], ref_name_suffix: str = ""
    ) -> dict[str, str]:
        # Iterate to a fixed point so renames cascade through cross-references:
        # if def B is renamed because it differs from an existing B, any incoming
        # def A that references B must also be rewritten — and that rewrite can
        # in turn cause A to differ from the existing A and need its own rename.
        incoming = dict(definitions)
        ref_renames: dict[str, str] = {}
        while True:
            for schema in incoming.values():
                self.rename_schema_refs(schema, ref_renames)

            new_renames = False
            for name, schema in incoming.items():
                if name in ref_renames:
                    continue
                existing = self.schemas.get(name)
                if existing is None or existing == schema:
                    continue
                ref_renames[name] = self._unique_schema_name(
                    name, ref_name_suffix, schema
                )
                new_renames = True

            if not new_renames:
                break

        for name, schema in incoming.items():
            final_name = ref_renames[name] if name in ref_renames else name
            self.schemas[final_name] = schema

        return ref_renames

    def _unique_schema_name(
        self, name: str, suffix: str, schema: dict[str, Any]
    ) -> str:
        candidate = f"{name}{suffix}" if suffix else f"{name}_2"
        index = 2
        while candidate in self.schemas and self.schemas[candidate] != schema:
            index += 1
            candidate = (
                f"{name}{suffix}_{index}" if suffix else f"{name}_{index}"
            )
        return candidate


def flatten_properties(
    prop_name: str,
    prop_details: dict[str, Any],
    prop_required: bool,
    definitions: dict[str, Any],
) -> Generator[tuple[str, dict[str, Any], bool], None, None]:
    """
    extracts all nested model's properties into flat properties
    (used f.e. in GET params with multiple arguments and models)
    """
    if "allOf" in prop_details:
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
