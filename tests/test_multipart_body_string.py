"""A ``str`` Body param sent as a multipart form field must round-trip verbatim.

When a ``Body(...)`` param is combined with a ``Form``/``File`` param the request
is multipart, and the body param arrives as a raw form-encoded string (not JSON).
That string must be preserved exactly — including interior quotes, leading/trailing
quotes, and newlines — instead of being naively wrapped in quotes (which produces
invalid JSON for anything but the simplest values).
"""

import pytest

from hattori import Body, Form, HattoriAPI, Schema
from hattori.testing import TestClient


class Resp(Schema):
    note: str
    tag: str


api = HattoriAPI()


@api.post("/mp")
def mp(request, note: str = Body(...), tag: str = Form("")) -> Resp:  # noqa: ARG001
    return {"note": note, "tag": tag}


client = TestClient(api)


@pytest.mark.parametrize(
    "value",
    [
        "hello",
        "with space",
        "12345",
        "",
        'ab"cd',  # interior quote
        '"leading',  # leading quote only
        'trailing"',  # trailing quote only
        '"quoted"',  # fully quoted -> preserved literally
        "line1\nline2",  # newline / control char
        "emoji 🚀 and unicode ü",
    ],
)
def test_str_body_param_roundtrips_verbatim_in_multipart(value):
    resp = client.post("/mp", POST={"note": value, "tag": "t"})
    assert resp.status_code == 200, resp.content
    assert resp.json() == {"note": value, "tag": "t"}
