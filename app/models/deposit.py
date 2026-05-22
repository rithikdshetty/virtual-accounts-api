"""
SQLAlchemy ORM model for the `deposits` table. Header record describing
an external deposit business event. The actual money movement lives in
the ledger_entries table.
"""
from datetime import datetime

from sqlalchemy import TIMESTAMP, BigInteger, Boolean, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.account import Base


class Deposit(Base):
    __tablename__ = "deposits"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    livemode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    rail: Mapped[str] = mapped_column(Text, nullable=False)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")
    )
    posted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
