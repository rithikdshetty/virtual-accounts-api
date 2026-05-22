"""
SQLAlchemy ORM model for the `transfers` table. The header record for
an internal money movement between two virtual accounts. The actual
ledger entries live in ledger_entries.
"""
from datetime import datetime

from sqlalchemy import JSON, TIMESTAMP, BigInteger, Boolean, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.account import Base


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    destination_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    livemode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    reverses_transfer_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, server_default="{}"
    )
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")
    )
    posted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
