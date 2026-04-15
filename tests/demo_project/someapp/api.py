from datetime import date

from django.shortcuts import get_object_or_404

from hattori import APIReturn, Router, Schema

from .models import Event

router = Router()


class EventSchema(Schema):
    title: str
    start_date: date
    end_date: date


class NoContent(APIReturn[None]):
    code = 204


def event_to_schema(event: "Event") -> EventSchema:
    return EventSchema(
        title=event.title,
        start_date=event.start_date,
        end_date=event.end_date,
    )


@router.post("/create", url_name="event-create-url-name")
def create_event(request, event: EventSchema) -> EventSchema:
    Event.objects.create(**event.model_dump())
    return event


@router.get("")
def list_events(request) -> list[EventSchema]:
    return [event_to_schema(e) for e in Event.objects.all()]


@router.delete("")
def delete_events(request) -> NoContent:
    Event.objects.all().delete()
    return NoContent(None)


@router.get("/{id}")
def get_event(request, id: int) -> EventSchema:
    return event_to_schema(get_object_or_404(Event, id=id))
