from django.conf import settings as django_settings
from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    FIX_REQUEST_FILES_METHODS: set[str] = Field(
        {"PUT", "PATCH", "DELETE"}, alias="HATTORI_FIX_REQUEST_FILES_METHODS"
    )


settings = Settings.model_validate(django_settings)

if hasattr(django_settings, "NINJA_DOCS_VIEW"):
    raise Exception(
        "NINJA_DOCS_VIEW is removed. Use HattoriAPI(docs=...) instead"
    )  # pragma: no cover
