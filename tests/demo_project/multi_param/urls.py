from django.urls import path

from hattori import HattoriAPI

from .api import router

api_multi_param = HattoriAPI(version="1.0.1")
api_multi_param.add_router("", router)

urlpatterns = [
    path("api/", api_multi_param.urls),
]
