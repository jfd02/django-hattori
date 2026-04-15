
from hattori import HattoriAPI, Schema
from hattori.security import HttpBearer


class TokenResponse(Schema):
    token: str


api = HattoriAPI()


class InvalidToken(Exception):
    pass


@api.exception_handler(InvalidToken)
def on_invalid_token(request, exc):
    return api.create_response(
        request, {"detail": "Invalid token supplied"}, status=401
    )


class AuthBearer(HttpBearer):
    def authenticate(self, request, token):
        if token == "supersecret":
            return token
        raise InvalidToken


@api.get("/bearer", auth=AuthBearer())
def bearer(request) -> TokenResponse:
    return {"token": request.auth}
