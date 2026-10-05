from typing import Any

from django.conf import settings as django_settings
from django.core.signals import setting_changed
from django.dispatch import receiver
from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    FIX_REQUEST_FILES_METHODS: set[str] = Field(
        {"PUT", "PATCH", "DELETE"}, alias="HATTORI_FIX_REQUEST_FILES_METHODS"
    )


settings = Settings.model_validate(django_settings)


@receiver(setting_changed)
def reload_hattori_settings(*args: Any, setting: str, **kwargs: Any) -> None:
    if not setting.startswith("HATTORI_"):
        return

    updated_settings = Settings.model_validate(django_settings)
    # Keep the object alive for modules that have already imported it.
    for field_name in Settings.model_fields:
        setattr(settings, field_name, getattr(updated_settings, field_name))


if hasattr(django_settings, "NINJA_DOCS_VIEW"):
    raise Exception(
        "NINJA_DOCS_VIEW is removed. Use HattoriAPI(docs=...) instead"
    )  # pragma: no cover
