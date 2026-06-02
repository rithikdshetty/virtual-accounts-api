"""
Pydantic schemas for withdrawals.
"""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


WithdrawalStatus = Literal["pending", "posted", "failed"]
Rail = Literal["ach", "wire", "rtp", "internal_test"]
WithdrawalFailureCode = Literal[
    "insufficient_funds", "account_frozen", "account_closed"
]


class WithdrawalCreateRequest(BaseModel):
    account_id: str = Field(..., min_length=1, max_length=255)
    amount: int = Field(..., ge=1, description="Minor units, positive integer")
    currency: str
    rail: Rail
    destination_reference: str = Field(..., min_length=1, max_length=255)

    @field_validator("currency")
    @classmethod
    def currency_iso4217(cls, v: str) -> str:
        if not re.match(r"^[A-Z]{3}$", v):
            raise ValueError("currency must be 3 uppercase letters (ISO 4217)")
        return v


class WithdrawalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    object: Literal["withdrawal"] = "withdrawal"
    account_id: str
    amount: int
    currency: str
    status: WithdrawalStatus
    livemode: bool
    rail: str
    destination_reference: str
    failure_code: WithdrawalFailureCode | None
    idempotency_key: str | None
    created_at: datetime
    posted_at: datetime | None


class WithdrawalListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[WithdrawalResponse]
    has_more: bool
    next_cursor: str | None = None
