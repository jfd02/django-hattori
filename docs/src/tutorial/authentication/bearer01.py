
from hattori import Schema
from hattori.security import HttpBearer


class TokenResponse(Schema):
    token: str


class AuthBearer(HttpBearer):
    def authenticate(self, request, token):
        if token == "supersecret":
            return token


@api.get("/bearer", auth=AuthBearer())
def bearer(request) -> TokenResponse:
    return {"token": request.auth}
