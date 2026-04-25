
from hattori import HattoriAPI, Router


def test_openapi_info_defined():
    "Test appending schema.info"
    extra_info = {
        "termsOfService": "https://example.com/terms/",
        "title": "Test API",
    }
    api = HattoriAPI(openapi_extra={"info": extra_info}, version="1.0.0")
    schema = api.get_openapi_schema()

    assert schema["info"]["termsOfService"] == "https://example.com/terms/"
    assert schema["info"]["title"] == "Test API"
    assert schema["info"]["version"] == "1.0.0"


def test_openapi_no_additional_info():
    api = HattoriAPI(title="Test API")
    schema = api.get_openapi_schema()

    assert schema["info"]["title"] == "Test API"
    assert "termsOfService" not in schema["info"]


def test_openapi_extra():
    "Test adding extra attribute to the schema"
    api = HattoriAPI(
        openapi_extra={
            "externalDocs": {
                "description": "Find more info here",
                "url": "https://example.com",
            }
        },
        version="1.0.0",
    )
    schema = api.get_openapi_schema()

    assert schema == {
        "openapi": "3.1.0",
        "info": {"title": "HattoriAPI", "version": "1.0.0", "description": ""},
        "paths": {},
        "components": {"schemas": {}},
        "servers": [],
        "externalDocs": {
            "description": "Find more info here",
            "url": "https://example.com",
        },
    }


def test_openapi_extra_deep_merges_into_responses():
    api = HattoriAPI()

    @api.get(
        "/x",
        openapi_extra={
            "responses": {"500": {"description": "Server error"}},
        },
    )
    def x(request) -> str:
        return "ok"

    schema = api.get_openapi_schema()
    responses = schema["paths"]["/api/x"]["get"]["responses"]
    assert 200 in responses
    assert responses["500"] == {"description": "Server error"}


def test_router_openapi_extra_extends():
    """
    Test for #1505.
    When adding an extra parameter to a route via openapi_extra, this should be combined with the route's own parameters.
    """
    api = HattoriAPI()
    test_router = Router()
    api.add_router("", test_router)

    extra_param = {
        "in": "header",
        "name": "X-HelloWorld",
        "required": False,
        "schema": {
            "type": "string",
            "format": "uuid",
        },
    }

    @test_router.get("/path/{item_id}", openapi_extra={"parameters": [extra_param]})
    def get_path_item_id(request, item_id: int) -> None:
        return None

    schema = api.get_openapi_schema()

    assert len(schema["paths"]["/api/path/{item_id}"]["get"]["parameters"]) == 2
    assert schema["paths"]["/api/path/{item_id}"]["get"]["parameters"] == [
        {
            "in": "path",
            "name": "item_id",
            "required": True,
            "schema": {
                "title": "Item Id",
                "type": "integer",
            },
        },
        extra_param,
    ]
