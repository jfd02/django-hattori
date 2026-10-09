"""What a return annotation declares.

An endpoint, an auth callback and a permission's ``check`` declare their
responses the same way, as arms of their return type, and are all read here.
"""

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import (
    Annotated,
    Any,
    TypeAliasType,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from hattori.compatibility.util import UNION_TYPES
from hattori.errors import ConfigError
from hattori.responses import APIReturn, resolve_api_return_schema

__all__ = [
    "DeclaredResponse",
    "DeclaredResponses",
    "alias_value",
    "declared_response",
    "declared_responses",
    "is_alias",
    "return_annotation_arms",
    "union_arms",
    "without_metadata",
]


def return_annotation_arms(target: Callable[..., Any]) -> tuple[Any, ...] | None:
    """The arms of ``target``'s return annotation, or ``None`` without one."""
    try:
        hints = get_type_hints(target, include_extras=True)
    except Exception:
        return None

    annotation = hints.get("return")
    if annotation is None:
        return None
    return tuple(union_arms(annotation))


def union_arms(annotation: Any) -> Iterator[Any]:
    """Each arm of ``annotation``, with every union in it opened up.

    A ``type`` alias counts as the type it names, and ``Annotated`` around a
    union as the union, so the arms behind either are read too. An alias whose
    value cannot be resolved stays an arm of its own.
    """
    if is_alias(annotation):
        try:
            value = alias_value(annotation)
        except Exception:
            # It names something only the type checker can see. The arms beside
            # it are still read.
            yield annotation
            return
        yield from union_arms(value)
    elif get_origin(annotation) in UNION_TYPES:
        for arm in get_args(annotation):
            yield from union_arms(arm)
    elif _is_union(without_metadata(annotation)):
        yield from union_arms(without_metadata(annotation))
    else:
        yield annotation


def _is_union(annotation: Any) -> bool:
    return is_alias(annotation) or get_origin(annotation) in UNION_TYPES


def is_alias(annotation: Any) -> bool:
    """Whether ``annotation`` is a ``type`` alias, plain or with its parameters."""
    return isinstance(annotation, TypeAliasType) or isinstance(
        get_origin(annotation), TypeAliasType
    )


def alias_value(annotation: Any) -> Any:
    """What a ``type`` alias names, a generic one with its parameters filled in.

    ``Page[Item]`` for ``type Page[T] = PageOf[T]`` is ``PageOf[Item]``.
    """
    if isinstance(annotation, TypeAliasType):
        return annotation.__value__
    alias = get_origin(annotation)
    type_params, args = alias.__type_params__, get_args(annotation)
    value = alias.__value__
    if type_params and args:
        value = _substitute_typevars(value, dict(zip(type_params, args, strict=False)))
    return value


def without_metadata(arm: Any) -> Any:
    """``arm`` without the ``Annotated`` it is written in, if it is in one."""
    return get_args(arm)[0] if get_origin(arm) is Annotated else arm


def _substitute_typevars(tp: Any, mapping: dict) -> Any:
    """Recursively substitute TypeVars in a type according to the mapping."""
    if tp in mapping:
        return mapping[tp]
    origin = get_origin(tp)
    args = get_args(tp)
    # Handle Pydantic models (get_args returns () but metadata has the args)
    if not args and origin is None:
        meta = getattr(tp, "__pydantic_generic_metadata__", None)
        if meta and meta.get("args"):
            origin = meta["origin"]
            args = meta["args"]
    if origin is None or not args:
        return tp
    new_args = tuple(_substitute_typevars(a, mapping) for a in args)
    return origin[new_args] if len(new_args) > 1 else origin[new_args[0]]


@dataclass(frozen=True)
class DeclaredResponse:
    """The response one ``APIReturn`` arm of a return annotation declares."""

    code: int
    body: Any
    description: str


def declared_response(arm: Any, owner: str) -> DeclaredResponse | None:
    """What ``arm`` declares, if it is an ``APIReturn``; ``None`` for any other.

    The arm is an ``APIReturn`` subclass, or a generic one with its body filled
    in, such as ``Created[UserOut]``. ``Annotated`` around either says nothing
    about the response. ``owner`` names what the annotation is on, for the
    error messages.
    """
    arm = without_metadata(arm)
    cls = get_origin(arm) or arm
    if not (isinstance(cls, type) and issubclass(cls, APIReturn)):
        return None
    code = getattr(cls, "code", None)
    if not isinstance(code, int):
        raise ConfigError(
            f"{cls.__name__} (in return type of {owner}) must define a "
            f"concrete `code: ClassVar[int]` on the class."
        )
    if cls is arm:
        try:
            body = resolve_api_return_schema(cls)
        except ValueError as e:
            raise ConfigError(str(e)) from e
    else:
        body = get_args(arm)[0]
    return DeclaredResponse(code, body, cls.description)


@dataclass
class DeclaredResponses:
    """The responses a return annotation declares, by status code.

    ``schemas`` is ``{code: body}``, the bodies of arms that share a code joined
    in a union. ``descriptions`` is ``{code: [description, ...]}`` in the order
    the arms are declared and without repeats; an arm that has none adds none.
    """

    schemas: dict[int, Any] = field(default_factory=dict)
    descriptions: dict[int, list[str]] = field(default_factory=dict)

    def add(self, code: int, body: Any, description: str = "") -> None:
        if code in self.schemas:
            # Not `|`, which not everything that names a body supports.
            body = Union[self.schemas[code], body]  # noqa: UP007
        self.schemas[code] = body
        if description:
            found = self.descriptions.setdefault(code, [])
            if description not in found:
                found.append(description)


def declared_responses(arms: Iterable[Any], owner: str) -> DeclaredResponses:
    """What the ``APIReturn`` arms among ``arms`` declare; the rest add nothing."""
    declared = DeclaredResponses()
    for arm in arms:
        response = declared_response(arm, owner)
        if response is not None:
            declared.add(response.code, response.body, response.description)
    return declared
