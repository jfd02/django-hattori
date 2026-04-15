
from hattori import Schema
from hattori.security import HttpBasicAuth


class AuthUser(Schema):
    httpuser: str


class BasicAuth(HttpBasicAuth):
    def authenticate(self, request, username, password):
        if username == "admin" and password == "secret":
            return username


@api.get("/basic", auth=BasicAuth())
def basic(request) -> AuthUser:
    return {"httpuser": request.auth}
