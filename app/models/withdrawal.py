"""
SQLAlchemy ORM model for the `withdrawals` table. Header record for an
external withdrawal (money leaving the system to an external destination).
The actual money movement lives in ledger_entries.
"""
from datetime import datetime

from sqlalchemy import TIMESTAMP, BigInteger, Boolean, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.account import Base


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    livemode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    rail: Mapped[str] = mapped_column(Text, nullable=False)
    destination_reference: Mapped[str] = mapped_column(Text, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")
    )
    posted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
