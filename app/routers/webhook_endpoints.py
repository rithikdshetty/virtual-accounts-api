"""
Webhook endpoint CRUD.

POST /webhook_endpoints
GET  /webhook_endpoints
GET  /webhook_endpoints/{id}
DELETE /webhook_endpoints/{id}

The secret is shown exactly once at creation. The DB stores both the
hash (for verification scenarios) and the raw secret (for server-side
HMAC signing of outbound payloads). In production, the raw column would
be encrypted at rest via a KMS-managed key. See NOTES.md.
"""
import hashlib
import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.lib.auth import require_api_key
from app.lib.idempotency import IdempotencyContext, require_idempotency_key
from app.lib.ids import new_id
from app.models import WebhookEndpoint
from app.schemas.webhook import (
    WebhookEndpointCreateRequest,
    WebhookEndpointListResponse,
    WebhookEndpointResponse,
    WebhookEndpointWithSecretResponse,
)


router = APIRouter(prefix="/webhook_endpoints", tags=["Webhooks"])


def _error(request: Request, status_code: int, code: str, title: str, detail: str | None = None):
    return HTTPException(
        status_code=status_code,
        detail={
            "type": f"https://api.example.com/errors/{code}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "code": code,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@router.post(
    "",
    response_model=WebhookEndpointWithSecretResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a webhook endpoint",
)
def create_endpoint(
    body: WebhookEndpointCreateRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    idem: Annotated[IdempotencyContext, Depends(require_idempotency_key)],
) -> Response:
    if idem.cached_response:
        return Response(
            content=json.dumps(idem.cached_response, default=str),
            status_code=idem.cached_status,
            media_type="application/json",
        )

    raw_secret = f"whsec_{secrets.token_urlsafe(32)}"

    endpoint = WebhookEndpoint(
        id=new_id("whk"),
        url=str(body.url),
        event_types=[t for t in body.event_types],
        secret_hash=_hash_secret(raw_secret),
        secret=raw_secret,
        status="active",
    )
    db.add(endpoint)
    db.flush()

    response_body = {
        "id": endpoint.id,
        "object": "webhook_endpoint",
        "url": endpoint.url,
        "event_types": endpoint.event_types,
        "status": endpoint.status,
        "created_at": endpoint.created_at.isoformat(),
        "secret": raw_secret,
    }

    idem.store(db, response_body, status_code=201)
    db.commit()

    return Response(
        content=json.dumps(response_body, default=str),
        status_code=201,
        media_type="application/json",
    )


@router.get(
    "",
    response_model=WebhookEndpointListResponse,
    summary="List webhook endpoints",
)
def list_endpoints(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    starting_after: Annotated[str | None, Query()] = None,
) -> WebhookEndpointListResponse:
    stmt = select(WebhookEndpoint).order_by(WebhookEndpoint.id)
    if starting_after:
        stmt = stmt.where(WebhookEndpoint.id > starting_after)
    stmt = stmt.limit(limit + 1)

    rows = list(db.execute(stmt).scalars())
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    return WebhookEndpointListResponse(
        data=[WebhookEndpointResponse.model_validate(r) for r in rows],
        has_more=has_more,
        next_cursor=rows[-1].id if has_more and rows else None,
    )


@router.get(
    "/{endpoint_id}",
    response_model=WebhookEndpointResponse,
    summary="Retrieve a webhook endpoint",
)
def get_endpoint(
    endpoint_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
) -> WebhookEndpointResponse:
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if not endpoint:
        raise _error(
            request, status.HTTP_404_NOT_FOUND, "webhook_endpoint_not_found",
            "Webhook endpoint not found",
        )
    return WebhookEndpointResponse.model_validate(endpoint)


@router.delete(
    "/{endpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a webhook endpoint",
)
def delete_endpoint(
    endpoint_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
) -> Response:
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if not endpoint:
        raise _error(
            request, status.HTTP_404_NOT_FOUND, "webhook_endpoint_not_found",
            "Webhook endpoint not found",
        )

    from sqlalchemy import update
    from app.models import WebhookDelivery
    db.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.endpoint_id == endpoint_id)
        .where(WebhookDelivery.status == "pending")
        .values(status="dead_lettered")
    )
    db.delete(endpoint)
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
