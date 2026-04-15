
from hattori import HattoriAPI, Router, Schema
from hattori.security import APIKeyQuery
from hattori.testing import TestClient


class KeyQuery1(APIKeyQuery):
    def authenticate(self, request, key):
        if key == "k1":
            return key


class KeyQuery2(APIKeyQuery):
    def authenticate(self, request, key):
        if key == "k2":
            return key


class AuthResult(Schema):
    auth: str


api = HattoriAPI(auth=KeyQuery1())


@api.get("/default")
def default(request) -> AuthResult:
    return {"auth": request.auth}


@api.api_operation(["POST", "PATCH"], "/multi-method-no-auth")
def multi_no_auth(request) -> AuthResult:
    return {"auth": request.auth}


@api.api_operation(["POST", "PATCH"], "/multi-method-auth", auth=KeyQuery2())
def multi_auth(request) -> AuthResult:
    return {"auth": request.auth}


# ---- router ------------------------

router = Router()


@router.get("/router-operation")  # should come from global auth
def router_operation(request) -> AuthResult:
    return {"auth": str(request.auth)}


@router.get("/router-operation-auth", auth=KeyQuery2())
def router_operation_auth(request) -> AuthResult:
    return {"auth": str(request.auth)}


api.add_router("", router)


router_noauth = Router(auth=None)


@router_noauth.get("/router-no-auth")
def router_operation_no_auth(request) -> AuthResult:
    return {"auth": str(request.auth)}


api.add_router("/no-auth/", router_noauth)

# ---- end router --------------------

client = TestClient(api)


def test_multi():
    assert client.get("/default").status_code == 401
    assert client.get("/default?key=k1").json() == {"auth": "k1"}

    assert client.post("/multi-method-no-auth").status_code == 401
    assert client.post("/multi-method-no-auth?key=k1").json() == {"auth": "k1"}

    assert client.patch("/multi-method-no-auth").status_code == 401
    assert client.patch("/multi-method-no-auth?key=k1").json() == {"auth": "k1"}

    assert client.post("/multi-method-auth?key=k1").status_code == 401
    assert client.patch("/multi-method-auth?key=k1").status_code == 401

    assert client.post("/multi-method-auth?key=k2").json() == {"auth": "k2"}
    assert client.patch("/multi-method-auth?key=k2").json() == {"auth": "k2"}


def test_router_auth():
    assert client.get("/router-operation").status_code == 401
    assert client.get("/router-operation?key=k1").json() == {"auth": "k1"}

    assert client.get("/router-operation-auth?key=k1").status_code == 401
    assert client.get("/router-operation-auth?key=k2").json() == {"auth": "k2"}


def test_router_no_auth():
    assert client.get("/no-auth/router-no-auth").json() == {"auth": "None"}
