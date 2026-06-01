"""
SQLAlchemy ORM model for the `events` table. Every state change in the
system writes a row here. Two consumers:
1. The webhook delivery worker (pushes to subscribed endpoints)
2. The GET /events endpoint (pull-based replay for missed deliveries)
"""
from datetime import datetime

from sqlalchemy import JSON, TIMESTAMP, Boolean, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.account import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    livemode: Mapped[bool] = mapped_column(Boolean, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")
    )
