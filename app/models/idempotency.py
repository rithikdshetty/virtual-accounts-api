"""
SQLAlchemy ORM model for the `idempotency_keys` table. Stores the request
hash and response for replay handling. Composite primary key on
(api_key_hash, idempotency_key) so two API keys using the same UUID don't
collide.
"""
from datetime import datetime

from sqlalchemy import JSON, TIMESTAMP, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.account import Base


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    api_key_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW() + INTERVAL '24 hours'"),
    )
