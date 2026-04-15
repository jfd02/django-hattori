
from hattori import HattoriAPI, Form, Schema
from hattori.security import HttpBearer


class GlobalAuth(HttpBearer):
    def authenticate(self, request, token):
        if token == "supersecret":
            return token


class TokenResponse(Schema):
    token: str


api = HattoriAPI(auth=GlobalAuth())

# @api.get(...)
# def ...
# @api.post(...)
# def ...


@api.post("/token", auth=None)  # < overriding global auth
def get_token(
    request, username: str = Form(...), password: str = Form(...)
) -> TokenResponse:
    if username == "admin" and password == "giraffethinnknslong":
        return {"token": "supersecret"}
