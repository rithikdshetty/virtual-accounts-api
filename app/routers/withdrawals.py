"""
Withdrawals endpoint. Records outbound external transfers: money leaving
the system to an external destination (another bank, etc).

The double-entry pattern, mirror of deposits:
  - Credit (decrease) the FBO cash account: -amount on fbo_cash_USD
  - Debit (decrease) the customer account:  -amount on acct_xxx
  Both entries NEGATIVE. The invariant (liabilities = FBO cash) holds
  because both sides decrease by the same amount.

Unlike deposits, withdrawals can FAIL on insufficient funds, so we use
the same row-lock-and-check pattern as transfers: SELECT FOR UPDATE on
the customer account, then check balance under the lock.

For v0.1 withdrawals post synchronously (status='posted'). A real bank
integration would start 'pending' and post on rail confirmation, with a
'failed' path on rail rejection. That state machine is v0.2 roadmap.

Emits a `withdrawal.posted` event on success.
"""
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.lib.auth import require_api_key
from app.lib.events import emit_event
from app.lib.idempotency import IdempotencyContext, require_idempotency_key
from app.lib.ids import new_id
from app.models import Account, LedgerEntry, Withdrawal
from app.schemas.withdrawal import (
    WithdrawalCreateRequest,
    WithdrawalListResponse,
    WithdrawalResponse,
)


router = APIRouter(prefix="/withdrawals", tags=["Withdrawals"])


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


def _compute_balance(db: Session, account_id: str) -> int:
    return db.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.account_id == account_id
        )
    ).scalar_one()


@router.post(
    "",
    response_model=WithdrawalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an external withdrawal",
)
def create_withdrawal(
    body: WithdrawalCreateRequest,
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

    # Lock the customer account row for the balance check.
    account = db.execute(
        select(Account).where(Account.id == body.account_id).with_for_update()
    ).scalar_one_or_none()
    if not account:
        raise _error(
            request, status.HTTP_404_NOT_FOUND, "account_not_found",
            "Account not found", f"No account with id={body.account_id}",
        )

    if account.status == "frozen":
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "account_frozen",
            "Account is frozen",
        )
    if account.status == "closed":
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "account_closed",
            "Account is closed",
        )
    if account.status != "active":
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "account_not_active",
            f"Account is {account.status}",
        )

    if account.currency != body.currency:
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "currency_mismatch",
            "Currency mismatch",
            f"Account currency is {account.currency}, withdrawal specified {body.currency}.",
        )

    fbo_id = f"fbo_cash_{body.currency}"
    fbo = db.get(Account, fbo_id)
    if not fbo:
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "fbo_not_configured",
            "FBO cash account not configured",
        )

    # Balance check under lock
    current_balance = _compute_balance(db, body.account_id)
    if current_balance < body.amount:
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "insufficient_funds",
            "Insufficient funds",
            f"Account balance is {current_balance}; withdrawal requested {body.amount}.",
        )

    withdrawal_id = new_id("wdl")
    now = datetime.now(timezone.utc)

    withdrawal = Withdrawal(
        id=withdrawal_id,
        account_id=body.account_id,
        amount=body.amount,
        currency=body.currency,
        status="posted",
        livemode=settings.livemode,
        rail=body.rail,
        destination_reference=body.destination_reference,
        idempotency_key=idem.key,
        created_at=now,
        posted_at=now,
    )

    # Two paired ledger entries. Both NEGATIVE.
    customer_entry = LedgerEntry(
        id=new_id("le"),
        account_id=body.account_id,
        amount=-body.amount,
        currency=body.currency,
        entry_type="withdrawal",
        related_withdrawal_id=withdrawal_id,
        description=f"Withdrawal via {body.rail}",
        posted_at=now,
    )
    fbo_entry = LedgerEntry(
        id=new_id("le"),
        account_id=fbo_id,
        amount=-body.amount,
        currency=body.currency,
        entry_type="fbo_cash",
        related_withdrawal_id=withdrawal_id,
        description=f"Withdrawal via {body.rail}",
        posted_at=now,
    )

    db.add_all([withdrawal, customer_entry, fbo_entry])
    db.flush()

    response_body = WithdrawalResponse.model_validate(withdrawal).model_dump(mode="json")

    emit_event(db, "withdrawal.posted", response_body)
    idem.store(db, response_body, status_code=201)

    db.commit()

    return Response(
        content=json.dumps(response_body, default=str),
        status_code=201,
        media_type="application/json",
    )


@router.get(
    "/{withdrawal_id}",
    response_model=WithdrawalResponse,
    summary="Retrieve a withdrawal",
)
def get_withdrawal(
    withdrawal_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
) -> WithdrawalResponse:
    withdrawal = db.get(Withdrawal, withdrawal_id)
    if not withdrawal:
        raise _error(
            request, status.HTTP_404_NOT_FOUND, "withdrawal_not_found",
            "Withdrawal not found",
        )
    return WithdrawalResponse.model_validate(withdrawal)


@router.get(
    "",
    response_model=WithdrawalListResponse,
    summary="List withdrawals",
)
def list_withdrawals(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    starting_after: Annotated[str | None, Query()] = None,
    account_id: Annotated[str | None, Query()] = None,
) -> WithdrawalListResponse:
    stmt = select(Withdrawal).order_by(Withdrawal.id.desc())
    if starting_after:
        stmt = stmt.where(Withdrawal.id < starting_after)
    if account_id:
        stmt = stmt.where(Withdrawal.account_id == account_id)
    stmt = stmt.limit(limit + 1)

    rows = list(db.execute(stmt).scalars())
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    return WithdrawalListResponse(
        data=[WithdrawalResponse.model_validate(r) for r in rows],
        has_more=has_more,
        next_cursor=rows[-1].id if has_more and rows else None,
    )
