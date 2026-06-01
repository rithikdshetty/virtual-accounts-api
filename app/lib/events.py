"""
Event emission helper. Called from routers when a business event happens.

Pattern: every time a Transfer, Deposit, etc. posts successfully, we
also write an Event row in the same DB transaction. This guarantees
"event emitted if and only if the underlying business event happened"
— no events for rolled-back transactions, no missing events for
committed ones.

In production at scale, this would use the "outbox pattern": a
dedicated outbox table polled by a separate worker, decoupling event
emission from the main DB write. For v0.1, writing directly to the
events table inside the same transaction works fine.

Pending webhook deliveries are also created in the same transaction:
one delivery row per (event, active subscribed endpoint) pair. The
delivery worker picks these up later.
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.lib.ids import new_id
from app.models import Event, WebhookDelivery, WebhookEndpoint


def emit_event(
    db: Session, event_type: str, data: dict[str, Any]
) -> Event:
    """
    Write an Event row and create pending WebhookDelivery rows for every
    active endpoint subscribed to this event type. Caller commits the
    surrounding transaction.

    The data dict should be the JSON-serialized form of the resource
    that triggered the event (e.g. the Transfer response body).
    """
    event = Event(
        id=new_id("evt"),
        event_type=event_type,
        livemode=settings.livemode,
        data=data,
    )
    db.add(event)
    db.flush()  # so event.id is materialized for FK use below

    # Find subscribed endpoints
    endpoints = list(
        db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.status == "active"
            )
        )
        .scalars()
    )

    now = datetime.now(timezone.utc)
    for ep in endpoints:
        # JSON column: event_types is a list. Check membership in Python.
        subscribed = ep.event_types if isinstance(ep.event_types, list) else []
        if event_type not in subscribed:
            continue
        delivery = WebhookDelivery(
            id=new_id("whd"),
            endpoint_id=ep.id,
            event_id=event.id,
            status="pending",
            attempt_count=0,
            next_attempt_at=now,  # ready immediately
        )
        db.add(delivery)

    return event
