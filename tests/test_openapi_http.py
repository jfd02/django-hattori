"""Generate requests from the served OpenAPI document and validate real HTTP responses."""

import pytest
import schemathesis
from django.test import override_settings
from hypothesis import settings
from pydantic import TypeAdapter, ValidationError
from schemathesis.core.parameters import ParameterLocation
from schemathesis.specs.openapi.checks import negative_data_rejection

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def api_schema(live_server):
    config = schemathesis.Config.from_dict({
        "generation": {"mode": "all", "max-examples": settings.default.max_examples},
        "operations": [
            {
                "include-path": "/api/error",
                "checks": {
                    "positive_data_acceptance": {"expected-statuses": [200, 422]}
                },
            },
            {
                "include-path": "/api/upload",
                # Multipart stringification can turn invalid generated objects
                # into valid text fields. Keep all response and positive checks;
                # required-file rejection is covered explicitly below.
                "checks": {"negative_data_rejection": {"enabled": False}},
            },
        ],
    })
    with override_settings(
        ROOT_URLCONF="tests.schemathesis_app",
        ALLOWED_HOSTS=["localhost", "testserver"],
        DEBUG=False,
    ):
        yield schemathesis.openapi.from_url(
            f"{live_server.url}/api/openapi.json", config=config
        )


schema = schemathesis.pytest.from_fixture("api_schema")
integer = TypeAdapter(int)


def _coercible_integer_path(case):
    # Schemathesis recognizes int("0") but not Pydantic's int-like strings
    # such as "+0.0". Limit the exception to this unconstrained integer path,
    # and only when no other request component was generated as invalid.
    if case.path != "/api/items/{item_id}" or case.meta is None:
        return False
    negative_locations = {
        location
        for location, info in case.meta.components.items()
        if info.mode.is_negative
    }
    if negative_locations != {ParameterLocation.PATH}:
        return False
    value = (case.path_parameters or {}).get("item_id")
    if not isinstance(value, str):
        return False
    try:
        integer.validate_python(value)
    except ValidationError:
        return False
    return True


@schema.parametrize()
def test_openapi_http(case):
    excluded = [negative_data_rejection] if _coercible_integer_path(case) else []
    case.call_and_validate(excluded_checks=excluded)


@pytest.mark.parametrize(
    ("value", "status"), [("+0.0", 200), ("-0.0", 200), ("abc", 422), ("0.5", 422)]
)
def test_integer_path_coercion(api_schema, value, status):
    case = api_schema["/api/items/{item_id}"]["GET"].Case(
        path_parameters={"item_id": value}
    )
    response = case.call()
    assert response.status_code == status
    if status == 200:
        assert response.json() == 10
    case.validate_response(response)


def test_missing_upload_is_rejected(api_schema):
    case = api_schema["/api/upload"]["POST"].Case(
        body={"text": "no file"}, media_type="multipart/form-data"
    )
    response = case.call()
    assert response.status_code == 422
    case.validate_response(response)


def test_empty_upload_is_accepted(api_schema):
    case = api_schema["/api/upload"]["POST"].Case(
        body={"file": b""}, media_type="multipart/form-data"
    )
    response = case.call()
    assert response.status_code == 200
    assert response.json() == 0
    case.validate_response(response)
