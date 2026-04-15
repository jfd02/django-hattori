
from hattori.security import APIKeyCookie


class CookieKey(APIKeyCookie):
    def authenticate(self, request, key):
        if key == "supersecret":
            return key


cookie_key = CookieKey()


@api.get("/cookiekey", auth=cookie_key, url_name="apikey_cookie")
def apikey(request) -> str:
    return f"Token = {request.auth}"
