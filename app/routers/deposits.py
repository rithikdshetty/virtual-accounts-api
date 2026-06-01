"""
Deposits endpoint. Records inbound external funds into a virtual account.

Emits a `deposit.posted` event on success, which fans out via webhooks
and is available via GET /events.
"""
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.lib.events import emit_event
from app.lib.idempotency import IdempotencyContext, require_idempotency_key
from app.lib.ids import new_id
from app.models import Account, Deposit, LedgerEntry
from app.schemas.deposit import DepositCreateRequest, DepositResponse


router = APIRouter(prefix="/deposits", tags=["Deposits"])


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


@router.post(
    "",
    response_model=DepositResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an external deposit",
)
def create_deposit(
    body: DepositCreateRequest,
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

    account = db.get(Account, body.account_id)
    if not account:
        raise _error(
            request, status.HTTP_404_NOT_FOUND, "account_not_found",
            "Account not found", f"No account with id={body.account_id}",
        )
    if account.status in ("frozen", "closed"):
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "account_not_active",
            f"Account is {account.status}",
            "Deposits to frozen or closed accounts are rejected.",
        )
    if account.currency != body.currency:
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "currency_mismatch",
            "Currency mismatch",
            f"Account currency is {account.currency}, deposit specified {body.currency}.",
        )

    fbo_id = f"fbo_cash_{body.currency}"
    fbo = db.get(Account, fbo_id)
    if not fbo:
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "fbo_not_configured",
            "FBO cash account not configured",
            f"No FBO cash account configured for currency {body.currency}.",
        )

    deposit_id = new_id("dep")
    now = datetime.now(timezone.utc)

    deposit = Deposit(
        id=deposit_id,
        account_id=body.account_id,
        amount=body.amount,
        currency=body.currency,
        status="posted",
        livemode=settings.livemode,
        rail=body.rail,
        source_reference=body.source_reference,
        idempotency_key=idem.key,
        created_at=now,
        posted_at=now,
    )

    fbo_entry = LedgerEntry(
        id=new_id("le"),
        account_id=fbo_id,
        amount=body.amount,
        currency=body.currency,
        entry_type="fbo_cash",
        related_deposit_id=deposit_id,
        description=f"Deposit via {body.rail}",
        posted_at=now,
    )
    customer_entry = LedgerEntry(
        id=new_id("le"),
        account_id=body.account_id,
        amount=body.amount,
        currency=body.currency,
        entry_type="deposit",
        related_deposit_id=deposit_id,
        description=f"Deposit via {body.rail}",
        posted_at=now,
    )

    db.add_all([deposit, fbo_entry, customer_entry])
    db.flush()

    response_body = DepositResponse.model_validate(deposit).model_dump(mode="json")

    # Emit event + create pending deliveries IN THE SAME TRANSACTION
    emit_event(db, "deposit.posted", response_body)

    idem.store(db, response_body, status_code=201)

    db.commit()

    return Response(
        content=json.dumps(response_body, default=str),
        status_code=201,
        media_type="application/json",
    )
