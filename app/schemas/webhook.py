"""
Pydantic schemas for webhook endpoint management.
"""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


EventType = Literal[
    "account.created",
    "account.status_changed",
    "transfer.posted",
    "transfer.failed",
    "deposit.posted",
    "withdrawal.posted",
    "withdrawal.failed",
]


class WebhookEndpointCreateRequest(BaseModel):
    url: HttpUrl
    event_types: list[EventType] = Field(..., min_length=1)


class WebhookEndpointResponse(BaseModel):
    """Returned for normal reads. Does NOT include the secret."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    object: Literal["webhook_endpoint"] = "webhook_endpoint"
    url: str
    event_types: list[str]
    status: str
    created_at: datetime


class WebhookEndpointWithSecretResponse(WebhookEndpointResponse):
    """
    Returned ONCE at creation. The raw secret is included so the client
    can save it. After this it's hashed in the DB and cannot be recovered.
    To rotate, delete and recreate the endpoint.
    """

    secret: str


class WebhookEndpointListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[WebhookEndpointResponse]
    has_more: bool
    next_cursor: str | None = None
