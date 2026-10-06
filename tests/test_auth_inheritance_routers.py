import pytest

from hattori import HattoriAPI, Router
from hattori.constants import NOT_SET
from hattori.security import APIKeyQuery
from hattori.testing import TestClient


class Auth(APIKeyQuery):
    def __init__(self, secret):
        self.secret = secret
        super().__init__()

    def authenticate(self, request, key):
        if key == self.secret:
            return key


api = HattoriAPI(auth=Auth("api_auth"))

r1 = Router()
r2 = Router()
r3 = Router()
r4 = Router()

o3 = Router(auth=None)
o4 = Router()

api.add_router("/r1", r1, auth=Auth("r1_auth"))
r1.add_router("/r2", r2)
r2.add_router("/r3", r3)
r3.add_router("/r4", r4, auth=Auth("r4_auth"))
r2.add_router("/o3", o3)
o3.add_router("/o4", o4)

client = TestClient(api)


@r1.get("/")
def op1(request) -> str:
    return request.auth


@r2.get("/")
def op2(request) -> str:
    return request.auth


@r3.get("/")
def op3(request) -> str:
    return request.auth


@r4.get("/")
def op4(request) -> str:
    return request.auth


@r3.get("/op5", auth=Auth("op5_auth"))
def op5(request) -> str:
    return request.auth


@o3.get("/")
def op_o3(request) -> str:
    assert request.auth is None
    return "ok"


@o4.get("/")
def op_o4(request) -> str:
    assert request.auth is None
    return "ok"


@pytest.mark.parametrize(
    "route, status_code",
    [
        ("/r1/", 401),
        ("/r1/r2/", 401),
        ("/r1/r2/r3/", 401),
        ("/r1/r2/r3/r4/", 401),
        ("/r1/r2/r3/op5", 401),
        ("/r1/?key=r1_auth", 200),
        ("/r1/r2/?key=r1_auth", 200),
        ("/r1/r2/r3/?key=r1_auth", 200),
        ("/r1/r2/r3/r4/?key=r4_auth", 200),
        ("/r1/r2/r3/op5?key=op5_auth", 200),
        ("/r1/r2/r3/r4/?key=r1_auth", 401),
        ("/r1/r2/r3/op5?key=r1_auth", 401),
        ("/r1/r2/o3/", 200),
        ("/r1/r2/o3/o4/", 200),
    ],
)
def test_router_inheritance_auth(route, status_code):
    assert client.get(route).status_code == status_code


# --------------------------------------------------------------------------
# Mount-level auth (add_router(..., auth=...)) on a router that has its own auth
# --------------------------------------------------------------------------


def make_parent(child_auth=NOT_SET) -> Router:
    """parent (auth="own") -> child -> grandchild, each with a single "/" route."""
    parent = Router(auth=Auth("own"))
    child = Router(auth=child_auth)
    grandchild = Router()

    @parent.get("/")
    def parent_op(request) -> str:
        return "ok"

    @child.get("/")
    def child_op(request) -> str:
        return "ok"

    @grandchild.get("/")
    def grandchild_op(request) -> str:
        return "ok"

    parent.add_router("/child", child)
    child.add_router("/grand", grandchild)
    return parent


def mount(parent: Router, nested: bool, mounts: dict) -> TestClient:
    """Mount parent once per prefix, on the API itself or on an intermediate router."""
    mounted_api = HattoriAPI()
    target = Router() if nested else mounted_api
    for prefix, auth in mounts.items():
        target.add_router(prefix, parent, auth=auth, url_name_prefix=prefix.strip("/"))
    if nested:
        mounted_api.add_router("", target)
    return TestClient(mounted_api)


mount_styles = pytest.mark.parametrize(
    "nested", [False, True], ids=["api.add_router", "router.add_router"]
)
tree_routes = pytest.mark.parametrize("route", ["/", "/child/", "/child/grand/"])


@mount_styles
@tree_routes
def test_mount_auth_replaces_router_auth_for_descendants(nested, route):
    client = mount(make_parent(), nested, {"/x": Auth("mount")})
    assert client.get(f"/x{route}?key=mount").status_code == 200
    assert client.get(f"/x{route}?key=own").status_code == 401


@mount_styles
@tree_routes
def test_mount_auth_none_reaches_descendants(nested, route):
    client = mount(make_parent(), nested, {"/x": None})
    assert client.get(f"/x{route}").status_code == 200


@mount_styles
def test_nested_router_own_auth_beats_ancestor_mount_auth(nested):
    parent = make_parent(child_auth=Auth("child"))
    client = mount(parent, nested, {"/x": Auth("mount")})
    assert client.get("/x/?key=mount").status_code == 200
    for route in ("/x/child/", "/x/child/grand/"):
        assert client.get(f"{route}?key=child").status_code == 200
        assert client.get(f"{route}?key=mount").status_code == 401


@mount_styles
@tree_routes
def test_mount_auth_is_separate_for_each_mount_of_the_same_router(nested, route):
    client = mount(make_parent(), nested, {"/a": Auth("a"), "/b": Auth("b")})
    assert client.get(f"/a{route}?key=a").status_code == 200
    assert client.get(f"/a{route}?key=b").status_code == 401
    assert client.get(f"/b{route}?key=b").status_code == 200
    assert client.get(f"/b{route}?key=a").status_code == 401
