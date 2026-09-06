"""``loc`` describes the request, not the handler's signature.

A single body param is wrapped under the handler's argument name internally so
one pydantic model can carry it, but the payload sits at the top level of the
request body. That argument name is private to the handler — renaming it must
not change what clients see.
"""

from hattori import Body, File, Form, HattoriAPI, Schema
from hattori.files import UploadedFile
from hattori.testing import TestClient


class Address(Schema):
    zip: str


class UserIn(Schema):
    email: str
    age: int
    address: Address | None = None


class Ok(Schema):
    ok: bool


api = HattoriAPI()


@api.post("/named-payload")
def named_payload(request, payload: UserIn) -> Ok:
    return Ok(ok=True)


@api.post("/named-differently")
def named_differently(request, whatever_i_called_it: UserIn) -> Ok:
    return Ok(ok=True)


@api.post("/listed")
def listed(request, users: list[UserIn]) -> Ok:
    return Ok(ok=True)


@api.post("/two-body-params")
def two_body_params(request, first: int = Body(...), second: int = Body(...)) -> Ok:
    return Ok(ok=True)


@api.post("/flattened-form")
def flattened_form(request, meta: UserIn = Form(...)) -> Ok:
    return Ok(ok=True)


@api.post("/multipart-body")
def multipart_body(
    request, meta: UserIn = Body(...), upload: UploadedFile = File(...)
) -> Ok:
    return Ok(ok=True)


client = TestClient(api)


def _locs(response):
    return [d["loc"] for d in response.json()["detail"]]


def test_body_loc_omits_handler_argument_name():
    r = client.post("/named-payload", json={"email": "a@b.c", "age": "not-an-int"})
    assert r.status_code == 422
    assert _locs(r) == [["body", "age"]]


def test_body_loc_is_stable_across_argument_renames():
    """The only difference between these two routes is the parameter name."""
    payload = {"email": "a@b.c", "age": "not-an-int"}
    assert _locs(client.post("/named-payload", json=payload)) == _locs(
        client.post("/named-differently", json=payload)
    )


def test_nested_body_loc_omits_handler_argument_name():
    r = client.post(
        "/named-payload",
        json={"email": "a@b.c", "age": 1, "address": {"zip": None}},
    )
    assert r.status_code == 422
    assert _locs(r) == [["body", "address", "zip"]]


def test_missing_required_body_field_loc():
    r = client.post("/named-payload", json={"email": "a@b.c"})
    assert r.status_code == 422
    assert _locs(r) == [["body", "age"]]


def test_list_body_loc_keeps_index_and_drops_argument_name():
    r = client.post("/listed", json=[{"email": "a@b.c", "age": "x"}])
    assert r.status_code == 422
    assert _locs(r) == [["body", 0, "age"]]


def test_multiple_body_params_keep_their_names():
    """With more than one body param the names *are* the wire contract, so
    they must survive."""
    r = client.post("/two-body-params", json={"first": 1})
    assert r.status_code == 422
    assert _locs(r) == [["body", "second"]]


def test_form_model_loc_uses_flattened_field_names():
    """A model behind Form() is flattened onto the form, so the argument name
    is already absent — the same contract the body branch now honours."""
    r = client.post(
        "/flattened-form", POST={"email": "a@b.c", "age": "x", "zip": "12345"}
    )
    assert r.status_code == 422
    assert _locs(r) == [["form", "age"]]


def test_multipart_body_param_keeps_its_name():
    """In a multipart request each body param is its own part, so the name is
    on the wire and must survive."""
    r = client.post("/multipart-body", POST={})
    assert r.status_code == 422
    assert ["body", "meta"] in _locs(r)
    assert ["file", "upload"] in _locs(r)
