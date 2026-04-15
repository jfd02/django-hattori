# Schemas

Hattori's `Schema` is a thin wrapper over `pydantic.BaseModel` with extra glue for Django (dotted attribute access, querysets, callable aliases). Most of what you know about pydantic models applies directly.

This page covers two schema-specific patterns that come up often.

## Self-referencing schemas

When a schema needs to reference itself (trees, nested categories, org charts), forward-reference the type in quotes and call `model_rebuild()` afterward:

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

## Serializing outside a view

Schemas work as standalone data classes too. Use `from_orm()` (a back-compat alias for `model_validate()`) to turn a Django model instance into a schema instance, then `.model_dump()` or `.model_dump_json()` for dict/JSON output:

```python
>>> person = Person.objects.get(id=1)
>>> data = PersonSchema.from_orm(person)
>>> data
PersonSchema(id=1, name="Mr. Smith")
>>> data.model_dump()
{"id": 1, "name": "Mr. Smith"}
>>> data.model_dump_json()
'{"id":1,"name":"Mr. Smith"}'
```

For a queryset or list:

```python
>>> persons = Person.objects.all()
>>> [PersonSchema.from_orm(p).model_dump() for p in persons]
[{"id": 1, "name": "Mr. Smith"}, {"id": 2, "name": "Mrs. Smith"}, ...]
```

This is useful for background jobs, management commands, or anywhere you need structured data without an HTTP request.
