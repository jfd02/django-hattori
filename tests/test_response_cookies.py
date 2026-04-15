
from django.http import HttpResponse

from hattori import HattoriAPI
from hattori.testing import TestClient

api = HattoriAPI()


@api.get("/test-no-cookies")
def op_no_cookies(request) -> None:
    return None


@api.get("/test-set-cookie")
def op_set_cookie(request) -> str:
    response = HttpResponse()
    response.set_cookie(key="sessionid", value="sessionvalue")
    return response  # HttpResponse pass-through


client = TestClient(api)


def test_cookies():
    assert bool(client.get("/test-no-cookies").cookies) is False
    assert "sessionid" in client.get("/test-set-cookie").cookies
