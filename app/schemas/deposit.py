"""
Pydantic schemas for deposits and balance/transaction reads.
"""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


DepositStatus = Literal["pending", "posted", "failed"]
Rail = Literal["ach", "wire", "rtp", "internal_test"]
TransactionType = Literal[
    "deposit", "withdrawal", "transfer_in", "transfer_out", "reversal", "fbo_cash"
]


class DepositCreateRequest(BaseModel):
    account_id: str = Field(..., min_length=1, max_length=255)
    amount: int = Field(..., ge=1, description="Minor units, positive integer")
    currency: str
    rail: Rail
    source_reference: str | None = None

    @field_validator("currency")
    @classmethod
    def currency_iso4217(cls, v: str) -> str:
        if not re.match(r"^[A-Z]{3}$", v):
            raise ValueError("currency must be 3 uppercase letters (ISO 4217)")
        return v


class DepositResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    object: Literal["deposit"] = "deposit"
    account_id: str
    amount: int
    currency: str
    status: DepositStatus
    livemode: bool
    rail: str
    source_reference: str | None
    idempotency_key: str | None
    created_at: datetime
    posted_at: datetime | None


class BalanceResponse(BaseModel):
    account_id: str
    currency: str
    available: int
    posted: int
    as_of: datetime


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    object: Literal["transaction"] = "transaction"
    account_id: str
    type: TransactionType
    amount: int
    currency: str
    balance_after: int
    related_transfer_id: str | None
    related_deposit_id: str | None
    related_withdrawal_id: str | None
    description: str | None
    posted_at: datetime


class TransactionListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[TransactionResponse]
    has_more: bool
    next_cursor: str | None = None
