"""
Deposits endpoint. Records inbound external funds into a virtual account.

The double-entry pattern in action:
  - Debit the FBO cash account (asset increases): +amount on fbo_cash_USD
  - Credit the customer account (liability increases): +amount on acct_xxx
  Both entries link to the same related_deposit_id.

For v0.1 deposits post synchronously (status='posted' on creation). In a
real integration with a bank rail, deposits would start 'pending' and
post on bank confirmation. State machine is on the v0.2 roadmap.

Transactions in this router use a single DB transaction wrapping all
inserts. If anything fails partway through, the entire operation rolls
back. The ledger never gets a half-posted deposit.

Note on timestamps: we set created_at / posted_at explicitly in Python
rather than relying on Postgres NOW() server defaults. Reason: we
serialize the response BEFORE committing (so it can be cached for
idempotency replay), and at that point Python-side attributes are still
None if we relied on the server default. Setting them in Python keeps
the response, the cache, and the DB row consistent.
"""
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
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
    # Idempotency replay path
    if idem.cached_response:
        return Response(
            content=json.dumps(idem.cached_response, default=str),
            status_code=idem.cached_status,
            media_type="application/json",
        )

    # Validate destination account
    account = db.get(Account, body.account_id)
    if not account:
        raise _error(
            request,
            status.HTTP_404_NOT_FOUND,
            "account_not_found",
            "Account not found",
            f"No account with id={body.account_id}",
        )
    if account.status in ("frozen", "closed"):
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "account_not_active",
            f"Account is {account.status}",
            "Deposits to frozen or closed accounts are rejected.",
        )
    if account.currency != body.currency:
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "currency_mismatch",
            "Currency mismatch",
            f"Account currency is {account.currency}, deposit specified {body.currency}.",
        )

    # Confirm FBO cash account exists for this currency
    fbo_id = f"fbo_cash_{body.currency}"
    fbo = db.get(Account, fbo_id)
    if not fbo:
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "fbo_not_configured",
            "FBO cash account not configured",
            f"No FBO cash account configured for currency {body.currency}.",
        )

    # Build the deposit and ledger entries within one transaction
    deposit_id = new_id("dep")
    now = datetime.now(timezone.utc)

    deposit = Deposit(
        id=deposit_id,
        account_id=body.account_id,
        amount=body.amount,
        currency=body.currency,
        status="posted",  # v0.1: synchronous post
        livemode=settings.livemode,
        rail=body.rail,
        source_reference=body.source_reference,
        idempotency_key=idem.key,
        created_at=now,
        posted_at=now,
    )

    # Two paired ledger entries.
    # Both POSITIVE: FBO cash goes up (asset increases) AND customer
    # liability goes up. The invariant for inbound external money is
    # asset = liability, not entries-sum-to-zero.
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

    db.flush()  # send pending changes to DB before further adds

    # Serialize response. All fields are now set in Python, so this
    # works before we commit.
    response_body = DepositResponse.model_validate(deposit).model_dump(mode="json")

    # Cache for future idempotency replays.
    idem.store(db, response_body, status_code=201)

    db.commit()

    return Response(
        content=json.dumps(response_body, default=str),
        status_code=201,
        media_type="application/json",
    )
