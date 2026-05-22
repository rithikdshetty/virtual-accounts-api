"""
Pydantic schemas for accounts. These define the JSON shapes coming in
and going out of the HTTP API. They are separate from the ORM models on
purpose: the wire format and the storage format have different concerns.
- Wire format cares about field naming, validation rules, OpenAPI docs.
- Storage format cares about column types, indexes, defaults.

Keeping them separate means you can change one without breaking the other.
"""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Match the OpenAPI spec exactly
AccountStatus = Literal["pending", "active", "frozen", "closed"]
AccountStatusMutable = Literal["active", "frozen", "closed"]


class AccountCreateRequest(BaseModel):
    """Body of POST /accounts."""

    customer_id: str = Field(..., min_length=1, max_length=255)
    currency: str = Field(..., description="ISO 4217 code, e.g. USD")
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def currency_must_be_iso4217(cls, v: str) -> str:
        if not re.match(r"^[A-Z]{3}$", v):
            raise ValueError("currency must be 3 uppercase letters (ISO 4217)")
        return v

    @field_validator("metadata")
    @classmethod
    def metadata_size_limit(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 50:
            raise ValueError("metadata may not have more than 50 keys")
        for key, val in v.items():
            if len(key) > 255 or len(val) > 500:
                raise ValueError(
                    "metadata keys must be <=255 chars, values <=500 chars"
                )
        return v


class AccountUpdateRequest(BaseModel):
    """Body of PATCH /accounts/{id}."""

    status: AccountStatusMutable | None = None
    metadata: dict[str, str] | None = None


class AccountResponse(BaseModel):
    """Response body for accounts. Matches OpenAPI spec field-for-field."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    object: Literal["account"] = "account"
    customer_id: str
    currency: str
    status: AccountStatus
    livemode: bool
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, account) -> "AccountResponse":
        """
        Convert an ORM Account row to a response. Handles the metadata_json
        -> metadata rename (DB column vs API field).
        """
        return cls(
            id=account.id,
            customer_id=account.customer_id,
            currency=account.currency,
            status=account.status,
            livemode=account.livemode,
            metadata=account.metadata_json or {},
            created_at=account.created_at,
            updated_at=account.updated_at,
        )


class AccountListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[AccountResponse]
    has_more: bool
    next_cursor: str | None = None


class ErrorResponse(BaseModel):
    """RFC 7807 problem+json shape, matching our OpenAPI spec."""

    type: str
    title: str
    status: int
    detail: str | None = None
    code: str
    request_id: str | None = None
