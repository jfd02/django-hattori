
from hattori import HattoriAPI
from hattori.security import django_auth

api = HattoriAPI()


@api.get("/pets", auth=django_auth)
def pets(request) -> str:
    return f"Authenticated user {request.auth}"
