
from hattori.security import APIKeyHeader


class ApiKey(APIKeyHeader):
    param_name = "X-API-Key"

    def authenticate(self, request, key):
        if key == "supersecret":
            return key


header_key = ApiKey()


@api.get("/headerkey", auth=header_key, url_name="apikey_header")
def apikey(request) -> str:
    return f"Token = {request.auth}"
