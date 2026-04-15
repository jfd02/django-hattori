# API Docs

## OpenAPI docs

Once you configured your Hattori API and started runserver -  go to [http://127.0.0.1:8000/api/docs](http://127.0.0.1:8000/api/docs)

You will see the automatic, interactive API documentation (provided by the [OpenAPI / Swagger UI](https://github.com/swagger-api/swagger-ui)


## CDN vs staticfiles

You are not required to put django hattori to `INSTALLED_APPS`. In that case the interactive UI is hosted by CDN.

To host docs (Js/css) from your own server - just put "hattori" to INSTALLED_APPS - in that case standard django staticfiles mechanics will host it.

## Switch to Redoc


```python
from hattori import Redoc

api = HattoriAPI(docs=Redoc())

```

Then you will see the alternative automatic documentation (provided by [Redoc](https://github.com/Redocly/redoc)).

## Changing docs display settings

To set some custom settings for Swagger or Redocs you can use `settings` param on the docs class

```python
from hattori import Redoc, Swagger

api = HattoriAPI(docs=Swagger(settings={"persistAuthorization": True}))
...
api = HattoriAPI(docs=Redoc(settings={"disableSearch": True}))

```

Settings reference:

 - [Swagger configuration](https://swagger.io/docs/open-source-tools/swagger-ui/usage/configuration/)
 - [Redoc configuration](https://redocly.com/docs/api-reference-docs/configuration/functionality/)



## Hiding docs

### Hiding the interactive docs viewer

To hide only the interactive documentation UI (Swagger or Redoc) while keeping the OpenAPI schema accessible, set `docs_url` to `None`:

```python
api = HattoriAPI(docs_url=None)
```

This disables the `/docs` endpoint but the OpenAPI schema remains available at `/openapi.json`. This is useful when you want to:

- Disable the interactive UI but keep the schema for API clients or code generators
- Use external documentation tools that consume the OpenAPI spec

### Disabling the OpenAPI schema endpoint

To disable the OpenAPI schema endpoint, set `openapi_url` to `None`:

```python
api = HattoriAPI(openapi_url=None)
```

This disables the `/openapi.json` endpoint. Since the docs viewer depends on the OpenAPI schema, this also disables the docs viewer - no documentation URLs will be registered.

### Summary

| Configuration | `/openapi.json` | `/docs` | Use Case |
|---------------|-----------------|---------|----------|
| Default | Available | Available | Development |
| `docs_url=None` | Available | Hidden | Hide UI, keep schema for clients |
| `openapi_url=None` | Hidden | Hidden | Completely hide all documentation |

## Protecting docs

To protect docs with authentication (or decorate for some other use case) use `docs_decorator` argument:

```python
from django.contrib.admin.views.decorators import staff_member_required

api = HattoriAPI(docs_decorator=staff_member_required)
```

## Extending OpenAPI Spec with custom attributes

You can extend OpenAPI spec with custom attributes, for example to add `termsOfService`

```python
api = HattoriAPI(
   openapi_extra={
       "info": {
           "termsOfService": "https://example.com/terms/",
       }
   },
   title="Demo API",
   description="This is a demo API with dynamic OpenAPI info section"
)
```

## Resolving the doc's url

The url for the api's documentation view can be reversed by referencing the view's name `openapi-view`.

In Python code, for example:
```python
from django.urls import reverse

reverse('api-1.0.0:openapi-view')

>>> '/api/docs'
```

In a Django template, for example:
```Html
<a href="{% url 'api-1.0.0:openapi-view' %}">API Docs</a>

<a href="/api/docs">API Docs</a>
```

## Creating custom docs viewer

To create your own view for OpenAPI - create a class inherited from DocsBase and overwrite `render_page` method:

```python
from hattori.openapi.docs import DocsBase

class MyDocsViewer(DocsBase):
    def render_page(self, request, api):
        ... # return http response

...

api = HattoriAPI(docs=MyDocsViewer())

```

## Using a custom favicon

The django-hattori OpenAPI docs contain a default favicon, the ninja star (a legacy icon from the original django-ninja project).
To use your own, overwrite the `hattori/favicons.html` django template.

```html
<!-- templates/hattori/favicons.html -->
{% load static %}

{% block favicons %}
    <link rel="icon" type="image/png" href="{% static 'path/to/your/favicon.png' %}">
{% endblock %}
```

for more information, see the [Django documentation on overriding templates](https://docs.djangoproject.com/en/5.2/howto/overriding-templates/).
