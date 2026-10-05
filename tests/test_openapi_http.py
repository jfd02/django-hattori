"""Generate requests from the served OpenAPI document and validate real HTTP responses."""

import pytest
import schemathesis
from django.test import override_settings
from hypothesis import settings

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


@schema.parametrize()
def test_openapi_http(case):
    case.call_and_validate()


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
