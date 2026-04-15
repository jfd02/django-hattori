
from hattori import Field, HattoriAPI, Schema


class SchemaWithAlias(Schema):
    foo: str = Field("", alias="bar")


api = HattoriAPI()


@api.get("/path")
def alias_operation(request) -> SchemaWithAlias:
    return {"bar": "value"}


def test_alias():
    schema = api.get_openapi_schema()["components"]
    print(schema)
    assert schema == {
        "schemas": {
            "SchemaWithAlias": {
                "type": "object",
                "properties": {
                    "foo": {"type": "string", "default": "", "title": "Foo"}
                },
                "title": "SchemaWithAlias",
            }
        }
    }


# TODO: check the conflicting approach
#       when alias is used both for response and request schema
#       basically it need to generate 2 schemas - one with alias another without
# @api.post("/path", response=SchemaWithAlias)
# def alias_operation(request, payload: SchemaWithAlias):
#     return {"bar": payload.foo}
