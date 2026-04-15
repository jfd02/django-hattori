from datetime import date

from django.shortcuts import get_object_or_404
from pydantic import BaseModel

from hattori import APIReturn, Router

from .models import Event

router = Router()


class EventSchema(BaseModel):
    model_config = dict(from_attributes=True)

    title: str
    start_date: date
    end_date: date


class NoContent(APIReturn[None]):
    code = 204


@router.post("/create", url_name="event-create-url-name")
def create_event(request, event: EventSchema) -> EventSchema:
    Event.objects.create(**event.model_dump())
    return event


@router.get("")
def list_events(request) -> list[EventSchema]:
    return list(Event.objects.all())


@router.delete("")
def delete_events(request) -> NoContent:
    Event.objects.all().delete()
    return NoContent(None)


@router.get("/{id}")
def get_event(request, id: int) -> EventSchema:
    event = get_object_or_404(Event, id=id)
    return event
