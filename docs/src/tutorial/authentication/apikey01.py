
from hattori.security import APIKeyQuery
from someapp.models import Client


class ApiKey(APIKeyQuery):
    param_name = "api_key"

    def authenticate(self, request, key):
        try:
            return Client.objects.get(key=key)
        except Client.DoesNotExist:
            pass


api_key = ApiKey()


@api.get("/apikey", auth=api_key, url_name="apikey_query")
def apikey(request) -> str:
    assert isinstance(request.auth, Client)
    return f"Hello {request.auth}"
