import pickle

import pydantic
import pytest

from hattori import Body, Header, HattoriAPI, Query, Schema
from hattori.errors import (
    HttpError,
    ValidationError,
    ValidationErrorDetail,
    ValidationErrorResponse,
)
from hattori.testing import TestClient


def test_validation_error_detail_accepts_str_and_int_loc():
    detail = ValidationErrorDetail(
        loc=["body", "items", 0, "name"], msg="required", type="missing"
    )
    assert detail.loc == ["body", "items", 0, "name"]


def test_validation_error_detail_rejects_invalid_loc():
    with pytest.raises(pydantic.ValidationError):
        ValidationErrorDetail(loc=[3.14], msg="bad", type="x")


def test_validation_error_response_structure():
    resp = ValidationErrorResponse(
        detail=[
            ValidationErrorDetail(
                loc=["query", "q"], msg="Field required", type="missing"
            ),
            ValidationErrorDetail(
                loc=["body", "items", 0], msg="Invalid", type="value_error"
            ),
        ]
    )
    assert len(resp.detail) == 2
    assert resp.detail[0].loc == ["query", "q"]
    assert resp.detail[1].loc == ["body", "items", 0]


def test_validation_error_response_matches_actual_422():
    """Round-trip: trigger a real 422 and verify the response parses into ValidationErrorResponse."""
    api = HattoriAPI()

    @api.get("/items")
    def get_items(request, count: int = Query(...)) -> list[int]:
        return []

    client = TestClient(api)
    response = client.get("/items?count=notanumber")
    assert response.status_code == 422

    # The actual response must parse cleanly into our documented model
    data = response.json()
    parsed = ValidationErrorResponse.model_validate(data)
    assert len(parsed.detail) >= 1
    error = parsed.detail[0]
    assert "query" in error.loc
    assert error.type == "int_parsing"


def test_422_with_combined_param_types():
    """Trigger validation errors across path + query + header + body simultaneously."""
    api = HattoriAPI()

    class Payload(Schema):
        name: str
        count: int

    @api.post("/items/{item_id}")
    def create_item(
        request,
        item_id: int,
        q: int = Query(...),
        x_tag: int = Header(...),
        body: Payload = Body(...),
    ) -> None:
        return None

    client = TestClient(api)

    # All four sources invalid at once
    response = client.post(
        "/items/notint?q=bad",
        json={"name": "ok", "count": "notint"},
        headers={"x-tag": "bad"},
    )
    assert response.status_code == 422
    data = response.json()
    parsed = ValidationErrorResponse.model_validate(data)

    sources = {err.loc[0] for err in parsed.detail}
    assert "path" in sources
    assert "query" in sources
    assert "header" in sources
    assert "body" in sources


def test_validation_error_is_picklable_and_unpicklable():
    error_to_serialize = ValidationError([{"testkey": "testvalue"}])

    serialized = pickle.dumps(error_to_serialize)
    assert serialized  # Not empty

    deserialized = pickle.loads(serialized)
    assert isinstance(deserialized, ValidationError)
    assert deserialized.errors == error_to_serialize.errors


def test_http_error_is_picklable_and_unpicklable():
    error_to_serialize = HttpError(500, "Test error")

    serialized = pickle.dumps(error_to_serialize)
    assert serialized  # Not empty

    deserialized = pickle.loads(serialized)
    assert isinstance(deserialized, HttpError)
    assert deserialized.status_code == error_to_serialize.status_code
    assert deserialized.message == error_to_serialize.message
