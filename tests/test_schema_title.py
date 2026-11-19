"""A schema that declares a `title` gets an OpenAPI component of that name.

Without one, a component is named after its Python class, which may not be the
name the API should use.
"""

from pydantic import ConfigDict, model_validator

from hattori import Body, HattoriAPI, Schema
from hattori.testing import TestClient
from tests.openapi_contract import export_contract, validate_response

REF = "#/components/schemas/{}"


class MoneySchema(Schema):
    model_config = ConfigDict(title="Money")

    amount: str


class Price(Schema, title="Cost"):
    total: int


class Refund(MoneySchema):
    reason: str


class Order(Schema):
    total: MoneySchema
    refunds: list[Refund]


class Money(Schema):
    cents: int


class Ledger(Schema):
    titled: MoneySchema
    named: Money


api = HattoriAPI()


@api.get("/orders/1")
def order(request) -> Order: ...


@api.get("/total")
def total(request) -> MoneySchema:
    return MoneySchema(amount="12.50")


@api.post("/totals")
def set_total(request, body: Body[MoneySchema]) -> None: ...


@api.get("/price")
def price(request) -> Price: ...


def schema() -> dict:
    return api.get_openapi_schema()


def response_schema(spec: dict, path: str) -> dict:
    method = next(iter(spec["paths"][f"/api{path}"].values()))
    return method["responses"][200]["content"]["application/json"]["schema"]


def test_nested_schema_is_named_by_its_title():
    spec = schema()
    components = spec["components"]["schemas"]

    assert "MoneySchema" not in components
    assert components["Money"]["title"] == "Money"
    assert components["Order"]["properties"]["total"] == {"$ref": REF.format("Money")}


def test_response_and_body_use_the_titled_component():
    spec = schema()

    assert response_schema(spec, "/total") == {"$ref": REF.format("Money")}
    body = spec["paths"]["/api/totals"]["post"]["requestBody"]
    assert body["content"]["application/json"]["schema"] == {
        "$ref": REF.format("Money")
    }


def test_title_can_be_a_class_keyword():
    spec = schema()

    assert response_schema(spec, "/price") == {"$ref": REF.format("Cost")}
    assert "Price" not in spec["components"]["schemas"]


def test_subclass_keeps_its_own_name():
    # Pydantic copies the parent's config, title included, into the subclass.
    # The subclass is a different schema, so it must not compete for the name
    # or carry it as its title.
    components = schema()["components"]["schemas"]

    assert components["Refund"]["title"] == "Refund"
    assert components["Order"]["properties"]["refunds"]["items"] == {
        "$ref": REF.format("Refund")
    }
    assert Refund.model_config.get("title") is None


def test_subclass_can_declare_its_own_title():
    class Payout(MoneySchema):
        model_config = ConfigDict(title="Disbursement")

        recipient: str

    class Statement(Schema):
        payout: Payout

    defs = Statement.json_schema()["$defs"]

    assert defs["Disbursement"]["title"] == "Disbursement"
    assert MoneySchema.model_config["title"] == "Money"


def test_json_schema_defs_use_the_title():
    defs = Order.json_schema()["$defs"]

    assert set(defs) == {"Money", "Refund"}


def test_title_shared_with_another_class_name_keeps_both_schemas():
    # Two schemas want "Money" in one document, so Pydantic falls back to
    # names that tell them apart, as it does for two classes of one name.
    defs = Ledger.json_schema()["$defs"]
    properties = Ledger.json_schema()["properties"]

    assert "Money" not in defs
    assert defs["MoneySchema"]["properties"] == {
        "amount": {"title": "Amount", "type": "string"}
    }
    named = properties["named"]["$ref"].rsplit("/", 1)[-1]
    assert defs[named]["properties"] == {"cents": {"title": "Cents", "type": "integer"}}


def test_response_matches_the_titled_component():
    document = export_contract(api)
    response = TestClient(api).get("/total")

    assert response.json() == {"amount": "12.50"}
    validate_response(document, "/api/total", response)


def test_title_names_a_schema_with_a_wrap_validator():
    # A wrap validator moves the schema's ref onto the node around it.
    class Checked(Schema, title="Verified"):
        amount: int

        @model_validator(mode="wrap")
        @classmethod
        def check(cls, data, handler):
            return handler(data)

    class Holder(Schema):
        checked: Checked

    assert set(Holder.json_schema()["$defs"]) == {"Verified"}


def test_title_names_a_schema_built_after_its_forward_refs_resolve():
    class Parcel(Schema, title="Package"):
        contents: Item

    class Item(Schema):
        name: str

    Parcel.model_rebuild()

    class Shipment(Schema):
        parcel: Parcel

    assert set(Shipment.json_schema()["$defs"]) == {"Package", "Item"}


def test_title_names_a_self_referencing_schema():
    class Node(Schema, title="TreeNode"):
        children: list[Node] = []

    schema = Node.json_schema()

    assert set(schema["$defs"]) == {"TreeNode"}
    assert schema["$ref"] == "#/$defs/TreeNode"
