from typing import Union

import pytest
from pydantic import ValidationError

from hattori import APIReturn, HattoriAPI, Schema
from hattori.testing import TestClient

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class UserModel(Schema):
    id: int
    name: str


class ErrorModel(Schema):
    detail: str


class Accepted(APIReturn[UserModel]):
    code = 202


class Redirect(APIReturn[str]):
    code = 300


class ClientError(APIReturn[float]):
    code = 400


class ServerError(APIReturn[float]):
    code = 500


class BadRequestErr(APIReturn[ErrorModel]):
    code = 400


class NoContent(APIReturn[None]):
    code = 204


api = HattoriAPI()


@api.get("/check_int")
def check_int(request) -> int:
    return "1"


@api.get("/check_int2")
def check_int2(request) -> int:
    return "str"


@api.get("/check_no_content")
def check_no_content(request) -> NoContent:
    return NoContent(None)


@api.get("/check_multiple_codes")
def check_multiple_codes(
    request, code: int
) -> int | Redirect | ClientError | ServerError:
    if code == 200:
        return 1
    if code == 300:
        return Redirect("1")
    if code == 400:
        return ClientError(1.0)
    return ServerError(1.0)


@api.get("/check_model")
def check_model(request) -> UserModel | Accepted:
    return Accepted(UserModel(id=1, name="John"))


@api.get("/check_list_model")
def check_list_model(request) -> list[UserModel]:
    return [UserModel(id=1, name="John")]


@api.get("/check_union")
def check_union(request, q: int) -> Union[int, UserModel] | BadRequestErr:
    if q == 0:
        return 1
    if q == 1:
        return UserModel(id=1, name="John")
    if q == 2:
        return BadRequestErr(ErrorModel(detail="error"))
    return "invalid"


client = TestClient(api)


@pytest.mark.parametrize(
    "path,expected_status,expected_response",
    [
        ("/check_int", 200, 1),
        ("/check_model", 202, {"id": 1, "name": "John"}),  # ! the password is skipped
        ("/check_list_model", 200, [{"id": 1, "name": "John"}]),
        ("/check_union?q=0", 200, 1),
        ("/check_union?q=1", 200, {"id": 1, "name": "John"}),
        ("/check_union?q=2", 400, {"detail": "error"}),
        ("/check_multiple_codes?code=200", 200, 1),
        ("/check_multiple_codes?code=300", 300, "1"),
        ("/check_multiple_codes?code=400", 400, 1.0),
        ("/check_multiple_codes?code=500", 500, 1.0),
    ],
)
def test_responses(path, expected_status, expected_response):
    response = client.get(path)
    assert response.status_code == expected_status, response.content
    assert response.json() == expected_response


def test_schema():
    checks = [
        ("/api/check_int", {200}),
        ("/api/check_int2", {200}),
        ("/api/check_model", {200, 202}),
        ("/api/check_list_model", {200}),
        ("/api/check_union", {200, 400, 422}),
    ]
    schema = api.get_openapi_schema()
    for path, codes in checks:
        responses = schema["paths"][path]["get"]["responses"]
        responses_codes = set(responses.keys())
        assert codes == responses_codes, f"{codes} != {responses_codes}"

    check_model_responses = schema["paths"]["/api/check_model"]["get"]["responses"]
    assert check_model_responses == {
        200: {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/UserModel"}
                }
            },
            "description": "OK",
        },
        202: {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/UserModel"}
                }
            },
            "description": "Accepted",
        },
    }


def test_no_content():
    response = client.get("/check_no_content")
    assert response.status_code == 204
    assert response.content == b""

    schema = api.get_openapi_schema()
    details = schema["paths"]["/api/check_no_content"]["get"]["responses"]
    assert 204 in details
    assert details[204] == {"description": "No Content"}


def test_validates():
    with pytest.raises(ValidationError):
        client.get("/check_int2")

    with pytest.raises(ValidationError):
        client.get("/check_union?q=3")
