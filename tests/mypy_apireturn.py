# Type-checks cleanly under mypy --strict. Exercises APIReturn end-user patterns.

from typing import ClassVar

from django.http import HttpRequest

from hattori import APIReturn, HattoriAPI, Schema


class UserOut(Schema):
    id: int
    name: str


class ErrorBody(Schema):
    code: str
    message: str


# User-level base that folds code/error_code/message into one constructor.
class AppError(APIReturn[ErrorBody]):
    code: ClassVar[int]
    error_code: ClassVar[str]
    message: ClassVar[str] = ""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            ErrorBody(
                code=self.error_code,
                message=message if message is not None else self.message,
            )
        )


class UserNotFound(AppError):
    code = 404
    error_code = "user_not_found"
    message = "User does not exist"


class Conflict1(AppError):
    code = 409
    error_code = "conflict_one"


class Conflict2(AppError):
    code = 409
    error_code = "conflict_two"


class NoContent(APIReturn[None]):
    code = 204


api = HattoriAPI()


@api.get("/bare")
def bare(request: HttpRequest) -> UserOut:
    return UserOut(id=1, name="a")


@api.get("/err/{id}")
def err(request: HttpRequest, id: int) -> UserOut | UserNotFound:
    if id == 0:
        return UserNotFound()
    return UserOut(id=id, name="a")


@api.get("/multi/{kind}")
def multi(
    request: HttpRequest, kind: str
) -> UserOut | UserNotFound | Conflict1 | Conflict2:
    if kind == "nf":
        return UserNotFound()
    if kind == "c1":
        return Conflict1("boom")
    if kind == "c2":
        return Conflict2()
    return UserOut(id=1, name="a")


@api.get("/empty/{ok}")
def empty(request: HttpRequest, ok: bool) -> UserOut | NoContent:
    if ok:
        return NoContent(None)
    return UserOut(id=1, name="a")
