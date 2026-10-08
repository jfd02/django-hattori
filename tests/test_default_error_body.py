"""``set_default_error_body`` reaches every error class whose body nothing has
needed yet, and none whose body something has already seen."""

import threading
from enum import Enum
from typing import Annotated, Literal

import pytest
from pydantic import PlainSerializer

from hattori import (
    ApiError,
    BasePermission,
    ErrorBody,
    HattoriAPI,
    NotFound,
    Schema,
    errors,
)
from hattori.errors import ConfigError, NarrowedBody
from hattori.http_errors import get_default_error_body, set_default_error_body
from hattori.responses import resolve_api_return_schema
from hattori.security import APIKeyQuery
from hattori.testing import TestClient


class Traced(ErrorBody):
    trace_id: str = "trace-1"


class Plain(ErrorBody):
    hint: str = "none"


class Out(Schema):
    id: int


class Kind(Enum):
    WIDGET = "widget_missing"


TRACED = {"trace_id": "trace-1"}


@pytest.fixture(autouse=True)
def restore_default():
    previous = get_default_error_body()
    yield
    set_default_error_body(previous)


def properties(api: HattoriAPI, name: str) -> list[str]:
    return list(api.get_openapi_schema()["components"]["schemas"][name]["properties"])


def test_error_class_defined_before_the_call_takes_the_default():
    class Early(ApiError):
        code = 400
        error_code = "early"
        message = "early"

    class WidgetMissing(NotFound[Literal[Kind.WIDGET]]):
        message = "no widget"

    set_default_error_body(Traced)

    assert Early().value.model_dump() == {
        "code": "early",
        "message": "early",
        **TRACED,
    }
    assert WidgetMissing(trace_id="t-2").value.model_dump() == {
        "code": "widget_missing",
        "message": "no widget",
        "trace_id": "t-2",
    }


def test_route_auth_and_permission_declared_after_the_call_all_use_the_default():
    class Early(ApiError):
        code = 400
        error_code = "early"
        message = "early"

    class BadKey(ApiError):
        code = 401
        error_code = "bad_key"
        message = "bad key"

    class NotStaff(ApiError):
        code = 403
        error_code = "not_staff"
        message = "not staff"

    set_default_error_body(Traced)

    class Key(APIKeyQuery):
        def authenticate(self, request, key) -> str | BadKey:
            return key or BadKey()

    class StaffOnly(BasePermission):
        def check(self, request) -> Literal[True] | NotStaff:
            return True if request.auth == "staff" else NotStaff()

    api = HattoriAPI(auth=Key(), permissions=[StaffOnly()])

    @api.get("/early")
    def early(request) -> Out | Early:
        return Early()

    client = TestClient(api)
    assert client.get("/early").json() == {
        "code": "bad_key",
        "message": "bad key",
        **TRACED,
    }
    assert client.get("/early?key=guest").json() == {
        "code": "not_staff",
        "message": "not staff",
        **TRACED,
    }
    assert client.get("/early?key=staff").json() == {
        "code": "early",
        "message": "early",
        **TRACED,
    }
    for name in ("Early", "BadKey", "NotStaff"):
        assert properties(api, name) == ["code", "message", "trace_id"]


def test_class_whose_body_a_route_has_seen_keeps_it():
    class Early(ApiError):
        code = 400
        error_code = "early"
        message = "early"

    api = HattoriAPI()

    @api.get("/early")
    def early(request) -> Out | Early:
        return Early()

    client = TestClient(api)
    set_default_error_body(Traced)

    # The route was declared under the old default, and what it sends, what it
    # documents and what the class builds still agree.
    assert client.get("/early").json() == {"code": "early", "message": "early"}
    assert properties(api, "Early") == ["code", "message"]
    assert Early().value.model_dump() == {"code": "early", "message": "early"}


@pytest.mark.parametrize(
    "see",
    [
        lambda error: error(),
        lambda error: resolve_api_return_schema(error),
        lambda error: error.__hattori_response_body__,
    ],
    ids=["instantiated", "resolved", "read"],
)
def test_body_is_settled_by_whatever_needs_it_first(see):
    class Early(ApiError):
        code = 400
        error_code = "early"
        message = "early"

    assert isinstance(vars(Early)["__hattori_response_body__"], NarrowedBody)
    see(Early)
    settled = vars(Early)["__hattori_response_body__"]
    assert issubclass(settled, ErrorBody)

    set_default_error_body(Traced)

    assert Early.__hattori_response_body__ is settled
    assert "trace_id" not in Early().value.model_dump()


def test_body_resolved_by_hand_stays_good_wherever_it_was_put():
    class Early(ApiError):
        code = 400
        error_code = "early"
        message = "early"

    body = resolve_api_return_schema(Early)
    api = HattoriAPI()

    @api.get("/listed")
    def listed(request) -> list[body]:
        return [Early().value]

    @api.get("/custom")
    def custom(
        request,
    ) -> Annotated[body, PlainSerializer(lambda value: "custom", return_type=str)]:
        return Early().value

    client = TestClient(api)
    for _ in range(2):
        assert client.get("/listed").json() == [{"code": "early", "message": "early"}]
        assert client.get("/custom").json() == "custom"
        set_default_error_body(Traced)


def test_subclass_without_a_code_of_its_own_answers_with_its_parents_body():
    class Early(ApiError):
        code = 400
        error_code = "early"
        message = "early"

    class Later(Early):
        """Declares no code of its own."""

    set_default_error_body(Traced)

    assert Later.__hattori_response_body__ is Early.__hattori_response_body__
    assert Later().value.model_dump()["trace_id"] == "trace-1"
    assert issubclass(vars(Early)["__hattori_response_body__"], Traced)


def test_body_a_class_names_itself_is_not_the_defaults_to_change():
    class Fixed(ApiError, body=Plain):
        code = 400
        error_code = "fixed"
        message = "fixed"

    class Inherits(Fixed):
        error_code = "inherits"

    set_default_error_body(Traced)

    for error in (Fixed, Inherits):
        assert error().value.model_dump() == {
            "code": error.error_code,
            "message": "fixed",
            "hint": "none",
        }


def test_default_can_be_set_and_put_back_around_classes_that_used_it():
    set_default_error_body(Traced)

    class During(ApiError):
        code = 400
        error_code = "during"
        message = "during"

    assert During().value.model_dump()["trace_id"] == "trace-1"
    set_default_error_body(ErrorBody)

    class After(ApiError):
        code = 400
        error_code = "after"
        message = "after"

    assert During().value.model_dump()["trace_id"] == "trace-1"
    assert After().value.model_dump() == {"code": "after", "message": "after"}


def test_everyone_asking_at_the_same_time_is_handed_the_same_body(monkeypatch):
    class Early(ApiError):
        code = 400
        error_code = "early"
        message = "early"

    narrow = errors.narrowed_error_body
    building, finish = threading.Event(), threading.Event()

    def held_while_others_ask(error, error_code):
        building.set()
        assert finish.wait(5)
        return narrow(error, error_code)

    monkeypatch.setattr(errors, "narrowed_error_body", held_while_others_ask)
    seen = []
    threads = [
        threading.Thread(target=lambda: seen.append(Early.__hattori_response_body__))
        for _ in range(8)
    ]
    threads[0].start()
    assert building.wait(5)
    # The rest ask while the first is still building it.
    for thread in threads[1:]:
        thread.start()
    finish.set()
    for thread in threads:
        thread.join()

    # However many of them built one, a single body was handed out.
    assert len(seen) == 8
    assert all(body is seen[0] for body in seen)
    assert vars(Early)["__hattori_response_body__"] is seen[0]


@pytest.mark.parametrize("asks_for", ["another", "itself"])
def test_building_a_body_holds_nothing_another_thread_is_waiting_for(asks_for):
    finished = []

    class WaitsOnAThread(ErrorBody):
        """A body whose subclasses, as they are made, wait on a thread that asks
        for an error's body."""

        @classmethod
        def __pydantic_init_subclass__(cls, **kwargs):
            super().__pydantic_init_subclass__(**kwargs)
            if finished:
                return
            finished.append(False)
            worker = threading.Thread(
                target=resolve_api_return_schema, args=(targets[asks_for],)
            )
            worker.start()
            worker.join(5)
            finished[0] = not worker.is_alive()

    class Another(ApiError):
        code = 400
        error_code = "another"
        message = "another"

    class Waiting(ApiError, body=WaitsOnAThread):
        code = 400
        error_code = "waiting"
        message = "waiting"

    targets = {"another": Another, "itself": Waiting}

    body = Waiting.__hattori_response_body__

    assert finished == [True]
    assert vars(Waiting)["__hattori_response_body__"] is body
    assert issubclass(body, WaitsOnAThread)


@pytest.mark.parametrize("asks_for", ["another", "itself"])
def test_putting_a_body_on_its_class_holds_nothing_either(asks_for):
    placed = []

    class Watching(type):
        """A metaclass that asks for an error's body when it is handed one."""

        def __setattr__(cls, name, value):
            if name == "__hattori_response_body__" and isinstance(value, type):
                placed.append(value)
                worker = threading.Thread(
                    target=resolve_api_return_schema, args=(targets[asks_for],)
                )
                worker.start()
                worker.join(5)
                assert not worker.is_alive()
                # And asks again itself, on this thread.
                resolve_api_return_schema(targets[asks_for])
            super().__setattr__(name, value)

    class Another(ApiError):
        code = 400
        error_code = "another"
        message = "another"

    class Watched(ApiError, metaclass=Watching):
        code = 400
        error_code = "watched"
        message = "watched"

    targets = {"another": Another, "itself": Watched}

    body = Watched.__hattori_response_body__

    # Placed once, and it is the body everyone was handed.
    assert placed == [body]
    assert vars(Watched)["__hattori_response_body__"] is body
    assert resolve_api_return_schema(Watched) is body


def test_body_asked_for_by_its_own_hooks_while_it_is_built_is_the_inherited_one():
    seen_meanwhile = []

    class AsksAgain(ErrorBody):
        """A body whose subclasses, every time one is made, ask for the bodies
        of these errors - the one being made among them."""

        @classmethod
        def __pydantic_init_subclass__(cls, **kwargs):
            super().__pydantic_init_subclass__(**kwargs)
            seen_meanwhile.append([resolve_api_return_schema(error) for error in asked])

    class Another(ApiError, body=AsksAgain):
        code = 400
        error_code = "another"
        message = "another"

    class Early(ApiError, body=AsksAgain):
        code = 400
        error_code = "early"
        message = "early"

    class Later(Early):
        error_code = "later"

    asked = [Early, Another, Later]

    body = Later.__hattori_response_body__

    # Each was built once, however often its hooks asked for it meanwhile, and
    # while it was being built it answered with the body it inherits.
    assert len(seen_meanwhile) == 3
    assert body is vars(Later)["__hattori_response_body__"]
    assert issubclass(body, AsksAgain)
    assert Later.__hattori_response_body__ is body
    assert Early.__hattori_response_body__ is vars(Early)["__hattori_response_body__"]
    assert seen_meanwhile[-1] == [
        Early.__hattori_response_body__,
        Another.__hattori_response_body__,
        Early.__hattori_response_body__,
    ]
    first_made = seen_meanwhile[0]
    assert ErrorBody in first_made


def test_default_set_by_a_bodys_own_hook_does_not_unsettle_the_body():
    class Early(ApiError):
        code = 400
        error_code = "early"
        message = "early"

    class Later(ErrorBody):
        trace_id: str = "later"

    seen_meanwhile = []

    class ChangesTheDefault(ErrorBody):
        """A default whose subclasses, as they are made, set another default
        and ask for the body again."""

        @classmethod
        def __pydantic_init_subclass__(cls, **kwargs):
            super().__pydantic_init_subclass__(**kwargs)
            set_default_error_body(Later)
            seen_meanwhile.append(resolve_api_return_schema(Early))

    set_default_error_body(ChangesTheDefault)
    body = Early.__hattori_response_body__

    # The hook was answered with the inherited body, as it is on a class whose
    # own is not made yet; the body that was being built is the one that stands.
    assert seen_meanwhile == [ErrorBody]
    assert issubclass(body, ChangesTheDefault)
    assert vars(Early)["__hattori_response_body__"] is body
    assert Early.__hattori_response_body__ is body


@pytest.mark.parametrize("not_a_body", [dict, Out, ErrorBody(code="c", message="m")])
def test_default_has_to_be_an_error_body(not_a_body):
    with pytest.raises(ConfigError, match="must subclass hattori.ErrorBody"):
        set_default_error_body(not_a_body)
