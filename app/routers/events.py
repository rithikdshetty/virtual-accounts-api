"""
Events endpoint. Customer-pullable replay of webhook events.

Marked experimental in the OpenAPI spec because the payload-typing
approach for the `data` field is still being finalized.

Retention: 30 days. Cleanup is a future job.
"""
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.lib.auth import require_api_key
from app.models import Event


router = APIRouter(prefix="/events", tags=["Webhooks"])


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    object: Literal["event"] = "event"
    type: str
    livemode: bool
    created_at: datetime
    data: dict

    @classmethod
    def from_model(cls, event: Event) -> "EventResponse":
        return cls(
            id=event.id,
            type=event.event_type,
            livemode=event.livemode,
            created_at=event.created_at,
            data=event.data,
        )


class EventListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[EventResponse]
    has_more: bool
    next_cursor: str | None = None


def _error(request: Request, status_code: int, code: str, title: str, detail: str | None = None):
    return HTTPException(
        status_code=status_code,
        detail={
            "type": f"https://api.example.com/errors/{code}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "code": code,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@router.get(
    "",
    response_model=EventListResponse,
    summary="List events (webhook replay)",
)
def list_events(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    starting_after: Annotated[str | None, Query()] = None,
    event_type: Annotated[list[str] | None, Query()] = None,
    created_after: Annotated[datetime | None, Query()] = None,
    created_before: Annotated[datetime | None, Query()] = None,
) -> EventListResponse:
    stmt = select(Event).order_by(Event.created_at.desc(), Event.id.desc())

    if starting_after:
        stmt = stmt.where(Event.id < starting_after)
    if event_type:
        stmt = stmt.where(Event.event_type.in_(event_type))
    if created_after:
        stmt = stmt.where(Event.created_at >= created_after)
    if created_before:
        stmt = stmt.where(Event.created_at < created_before)

    stmt = stmt.limit(limit + 1)
    rows = list(db.execute(stmt).scalars())
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    return EventListResponse(
        data=[EventResponse.from_model(r) for r in rows],
        has_more=has_more,
        next_cursor=rows[-1].id if has_more and rows else None,
    )


@router.get(
    "/{event_id}",
    response_model=EventResponse,
    summary="Retrieve a single event",
)
def get_event(
    event_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
) -> EventResponse:
    event = db.get(Event, event_id)
    if not event:
        raise _error(
            request, status.HTTP_404_NOT_FOUND, "event_not_found", "Event not found",
        )
    return EventResponse.from_model(event)
