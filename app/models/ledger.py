"""
SQLAlchemy ORM model for the `ledger_entries` table. The ledger is the
source of truth for all money in the system; the `transfers`, `deposits`,
`withdrawals` tables are headers describing business events while the
actual value movement lives here as paired entries.
"""
from datetime import datetime

from sqlalchemy import JSON, TIMESTAMP, BigInteger, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.account import Base


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    entry_type: Mapped[str] = mapped_column(Text, nullable=False)
    related_transfer_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_deposit_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_withdrawal_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")
    )
