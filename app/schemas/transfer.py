"""
Pydantic schemas for transfers. Match the OpenAPI spec field-for-field.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TransferStatus = Literal["posted", "failed", "reversed"]
TransferFailureCode = Literal[
    "insufficient_funds",
    "currency_mismatch",
    "account_frozen",
    "account_closed",
    "same_account",
]


class TransferCreateRequest(BaseModel):
    source_account_id: str = Field(..., min_length=1, max_length=255)
    destination_account_id: str = Field(..., min_length=1, max_length=255)
    amount: int = Field(
        ...,
        ge=1,
        description=(
            "Unsigned minor units. Currency is implied by the source account "
            "and must match the destination account."
        ),
    )
    description: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class ReversalCreateRequest(BaseModel):
    """Body for POST /transfers/{id}/reversal. Optional metadata only."""

    description: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class TransferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    object: Literal["transfer"] = "transfer"
    source_account_id: str
    destination_account_id: str
    amount: int
    currency: str
    status: TransferStatus
    livemode: bool
    reverses_transfer_id: str | None
    failure_code: TransferFailureCode | None
    description: str | None
    metadata: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str | None
    created_at: datetime
    posted_at: datetime | None

    @classmethod
    def from_model(cls, transfer) -> "TransferResponse":
        return cls(
            id=transfer.id,
            source_account_id=transfer.source_account_id,
            destination_account_id=transfer.destination_account_id,
            amount=transfer.amount,
            currency=transfer.currency,
            status=transfer.status,
            livemode=transfer.livemode,
            reverses_transfer_id=transfer.reverses_transfer_id,
            failure_code=transfer.failure_code,
            description=transfer.description,
            metadata=transfer.metadata_json or {},
            idempotency_key=transfer.idempotency_key,
            created_at=transfer.created_at,
            posted_at=transfer.posted_at,
        )


class TransferListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[TransferResponse]
    has_more: bool
    next_cursor: str | None = None
