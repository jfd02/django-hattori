from types import UnionType
from typing import Union

__all__ = ["UNION_TYPES"]


# python3.10+ syntax of creating a union or optional type (with str | int)
# UNION_TYPES allows to check both universes if types are a union
UNION_TYPES = (Union, UnionType)
