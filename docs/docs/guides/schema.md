# Schemas

Hattori's `Schema` is a thin `pydantic.BaseModel` wrapper — it's called `Schema` only because "Model" is already overloaded in Django. Everything you know about pydantic models applies directly: field validation, serialization, `ConfigDict`, custom validators, generics.

Hattori's Schema is **strict** by design: it does **not** set `from_attributes=True`, so it will not silently coerce arbitrary Django ORM instances into schema instances. That coupling — mapping an ORM record to an API shape — is yours to own, explicitly. See [Response Schema](response/index.md) for why.

## Self-referencing schemas

When a schema references itself (trees, nested categories, org charts), forward-reference the type in quotes and call `model_rebuild()` afterward:

```python hl_lines="3 6"
class Organization(Schema):
    title: str
    part_of: "Organization" = None     # forward reference in quotes


Organization.model_rebuild()            # resolves the forward reference


@api.get("/organizations")
def list_organizations(request) -> list[Organization]:
    ...
```

Without `model_rebuild()`, pydantic can't resolve the `"Organization"` string at class-definition time.

## Building schemas outside a view

Schemas work as standalone data classes wherever you need structured data — background jobs, management commands, tests. Just construct them explicitly from the underlying data:

```python
>>> person = Person.objects.get(id=1)
>>> data = PersonSchema(id=person.id, name=person.name)
>>> data.model_dump()
{"id": 1, "name": "Mr. Smith"}
>>> data.model_dump_json()
'{"id":1,"name":"Mr. Smith"}'
```

For a queryset, centralize the mapping in a function and reuse it everywhere:

```python
def person_to_schema(p: Person) -> PersonSchema:
    return PersonSchema(id=p.id, name=p.name)


>>> [person_to_schema(p).model_dump() for p in Person.objects.all()]
[{"id": 1, "name": "Mr. Smith"}, {"id": 2, "name": "Mrs. Smith"}, ...]
```

The explicit mapping makes the ORM → Schema coupling type-checked and grep-able. Renaming a Django field surfaces immediately as a type error instead of silently breaking serialization.
