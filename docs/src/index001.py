
from django.contrib import admin
from django.urls import path

from hattori import HattoriAPI, Schema


class AddResult(Schema):
    result: int


api = HattoriAPI()


@api.get("/add")
def add(request, a: int, b: int) -> AddResult:
    return {"result": a + b}


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
