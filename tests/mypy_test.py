# The goal of this file is to test that mypy "likes" all the combinations of parametrization

from django.http import HttpRequest
from typing_extensions import Annotated

from hattori import Body, BodyEx, HattoriAPI, P, Schema


class Payload(Schema):
    x: int
    y: float
    s: str


api = HattoriAPI()


@api.post("/old_way")
def old_way(
    request: HttpRequest, data: Payload = Body()
) -> None:
    data.s.capitalize()


@api.post("/annotated_way")
def annotated_way(
    request: HttpRequest, data: Annotated[Payload, Body()]
) -> None:
    data.s.capitalize()


@api.post("/new_way")
def new_way(request: HttpRequest, data: Body[Payload]) -> None:
    data.s.capitalize()


@api.post("/new_way_ex")
def new_way_ex(
    request: HttpRequest, data: BodyEx[Payload, P(title="A title")]
) -> None:
    data.s.find("")
