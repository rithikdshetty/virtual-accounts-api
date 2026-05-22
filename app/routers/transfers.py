"""
Transfers endpoint. Moves money atomically between two virtual accounts.

Critical differences from deposits:
- Both source and destination are customer accounts; FBO cash does NOT
  move on an internal transfer (the bank doesn't see it).
- Ledger entries net to zero across the two legs (one negative, one
  positive).
- Source account must have sufficient balance, checked under row lock.

Row locking: `SELECT ... FOR UPDATE` on the source account row inside
the transaction. This serializes concurrent transfers from the same
source so the balance check and the debit happen atomically. Without
the lock, two concurrent transfers could both pass the balance check
when only one should succeed (classic check-then-act race).

For deposits we didn't need the lock because there's no balance check;
deposits only add money. Transfers can fail on insufficient funds, so
the check-then-act sequence must be serialized.

Reversals are a sub-endpoint: POST /transfers/{id}/reversal. They post
a new Transfer with reverses_transfer_id set. The reversal validates
the original is in 'posted' state (not failed or already reversed) and
that the destination of the original (source of the reversal) has
sufficient balance.
"""
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.lib.auth import require_api_key
from app.lib.idempotency import IdempotencyContext, require_idempotency_key
from app.lib.ids import new_id
from app.models import Account, LedgerEntry, Transfer
from app.schemas.transfer import (
    ReversalCreateRequest,
    TransferCreateRequest,
    TransferListResponse,
    TransferResponse,
)


router = APIRouter(prefix="/transfers", tags=["Transfers"])


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
    """Sum of all ledger entries for an account."""
    return db.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.account_id == account_id
        )
    ).scalar_one()


# ---------- POST /transfers ----------

@router.post(
    "",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a transfer between virtual accounts",
)
def create_transfer(
    body: TransferCreateRequest,
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

    # Reject self-transfer at the application layer (the DB also has a
    # CHECK constraint, but we return a clean 422 here).
    if body.source_account_id == body.destination_account_id:
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "same_account",
            "Source and destination must differ",
        )

    # Fetch both accounts. Lock the source row for the balance check.
    # We lock the destination too just for consistency (catches the
    # case where the destination is frozen/closed between fetch and
    # commit, though our small window makes this nearly impossible).
    source_stmt = select(Account).where(Account.id == body.source_account_id).with_for_update()
    source = db.execute(source_stmt).scalar_one_or_none()
    if not source:
        raise _error(
            request,
            status.HTTP_404_NOT_FOUND,
            "account_not_found",
            "Source account not found",
        )

    destination_stmt = select(Account).where(Account.id == body.destination_account_id).with_for_update()
    destination = db.execute(destination_stmt).scalar_one_or_none()
    if not destination:
        raise _error(
            request,
            status.HTTP_404_NOT_FOUND,
            "account_not_found",
            "Destination account not found",
        )

    # Both accounts must be active (not frozen, not closed). Pending is
    # rejected too: a pending account hasn't been verified for use.
    if source.status != "active":
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "account_frozen" if source.status == "frozen" else "account_closed",
            f"Source account is {source.status}",
        )
    if destination.status != "active":
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "account_frozen" if destination.status == "frozen" else "account_closed",
            f"Destination account is {destination.status}",
        )

    if source.currency != destination.currency:
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "currency_mismatch",
            "Source and destination currencies differ",
            f"Source is {source.currency}, destination is {destination.currency}.",
        )

    # Balance check under lock. Because we hold a row lock on the source
    # account row, no concurrent transaction can change its balance
    # between our SUM query and our INSERTs below.
    current_balance = _compute_balance(db, body.source_account_id)
    if current_balance < body.amount:
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "insufficient_funds",
            "Insufficient funds",
            f"Source balance is {current_balance}; transfer requested {body.amount}.",
        )

    # Build the transfer + two ledger entries inside the same tx.
    transfer_id = new_id("tfr")
    now = datetime.now(timezone.utc)

    transfer = Transfer(
        id=transfer_id,
        source_account_id=body.source_account_id,
        destination_account_id=body.destination_account_id,
        amount=body.amount,
        currency=source.currency,
        status="posted",
        livemode=settings.livemode,
        description=body.description,
        metadata_json=body.metadata,
        idempotency_key=idem.key,
        created_at=now,
        posted_at=now,
    )

    # Two paired ledger entries. NET TO ZERO.
    # Source: amount is NEGATIVE (debit, balance decreases)
    # Destination: amount is POSITIVE (credit, balance increases)
    source_entry = LedgerEntry(
        id=new_id("le"),
        account_id=body.source_account_id,
        amount=-body.amount,
        currency=source.currency,
        entry_type="transfer_out",
        related_transfer_id=transfer_id,
        description=body.description,
        posted_at=now,
    )
    destination_entry = LedgerEntry(
        id=new_id("le"),
        account_id=body.destination_account_id,
        amount=+body.amount,
        currency=source.currency,
        entry_type="transfer_in",
        related_transfer_id=transfer_id,
        description=body.description,
        posted_at=now,
    )

    db.add_all([transfer, source_entry, destination_entry])
    db.flush()

    response_body = TransferResponse.from_model(transfer).model_dump(mode="json")
    idem.store(db, response_body, status_code=201)

    db.commit()

    return Response(
        content=json.dumps(response_body, default=str),
        status_code=201,
        media_type="application/json",
    )


# ---------- POST /transfers/{id}/reversal ----------

@router.post(
    "/{transfer_id}/reversal",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reverse a posted transfer",
)
def reverse_transfer(
    transfer_id: str,
    body: ReversalCreateRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    idem: Annotated[IdempotencyContext, Depends(require_idempotency_key)],
) -> Response:
    """
    Posts a new transfer that inverts the source and destination of the
    original. Validations:
    - Original must exist
    - Original must be in 'posted' status (not failed or reversed)
    - Original must not itself be a reversal (no reversal-of-reversal)
    - Destination of original (source of reversal) must have funds
    - Both accounts of the original must still be active

    The original transfer transitions to 'reversed' status when the
    reversal posts.
    """
    if idem.cached_response:
        return Response(
            content=json.dumps(idem.cached_response, default=str),
            status_code=idem.cached_status,
            media_type="application/json",
        )

    # Lock the original transfer row to prevent concurrent reversals.
    original_stmt = select(Transfer).where(Transfer.id == transfer_id).with_for_update()
    original = db.execute(original_stmt).scalar_one_or_none()
    if not original:
        raise _error(
            request,
            status.HTTP_404_NOT_FOUND,
            "transfer_not_found",
            "Transfer not found",
        )

    if original.reverses_transfer_id is not None:
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "cannot_reverse_reversal",
            "Cannot reverse a reversal",
        )

    if original.status != "posted":
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "transfer_not_posted",
            f"Transfer is {original.status}, not posted",
        )

    # The reversal moves money from original.destination back to
    # original.source. Lock both rows.
    src_for_reversal = db.execute(
        select(Account).where(Account.id == original.destination_account_id).with_for_update()
    ).scalar_one_or_none()
    dst_for_reversal = db.execute(
        select(Account).where(Account.id == original.source_account_id).with_for_update()
    ).scalar_one_or_none()

    if not src_for_reversal or not dst_for_reversal:
        raise _error(
            request,
            status.HTTP_404_NOT_FOUND,
            "account_not_found",
            "One or both accounts no longer exist",
        )

    if dst_for_reversal.status == "closed":
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "account_closed",
            "Original source account is closed",
        )

    # Frozen accounts can RECEIVE reversal funds (the goal is to undo
    # the original; we shouldn't block correction). They can also have
    # funds withdrawn for reversal purposes.
    # Closed source: cannot pull funds from a closed account.
    if src_for_reversal.status == "closed":
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "account_closed",
            "Cannot reverse; original destination account is closed",
        )

    current_balance = _compute_balance(db, src_for_reversal.id)
    if current_balance < original.amount:
        raise _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "insufficient_funds",
            "Insufficient funds to reverse",
            f"Destination of original has balance {current_balance}; need {original.amount}.",
        )

    reversal_id = new_id("tfr")
    now = datetime.now(timezone.utc)

    reversal = Transfer(
        id=reversal_id,
        source_account_id=original.destination_account_id,  # inverted
        destination_account_id=original.source_account_id,  # inverted
        amount=original.amount,
        currency=original.currency,
        status="posted",
        livemode=settings.livemode,
        reverses_transfer_id=original.id,
        description=body.description or f"Reversal of {original.id}",
        metadata_json=body.metadata,
        idempotency_key=idem.key,
        created_at=now,
        posted_at=now,
    )

    rev_source_entry = LedgerEntry(
        id=new_id("le"),
        account_id=original.destination_account_id,
        amount=-original.amount,
        currency=original.currency,
        entry_type="reversal",
        related_transfer_id=reversal_id,
        description=f"Reversal of {original.id}",
        posted_at=now,
    )
    rev_destination_entry = LedgerEntry(
        id=new_id("le"),
        account_id=original.source_account_id,
        amount=+original.amount,
        currency=original.currency,
        entry_type="reversal",
        related_transfer_id=reversal_id,
        description=f"Reversal of {original.id}",
        posted_at=now,
    )

    # Mark the original as reversed.
    original.status = "reversed"

    db.add_all([reversal, rev_source_entry, rev_destination_entry])
    db.flush()

    response_body = TransferResponse.from_model(reversal).model_dump(mode="json")
    idem.store(db, response_body, status_code=201)

    db.commit()

    return Response(
        content=json.dumps(response_body, default=str),
        status_code=201,
        media_type="application/json",
    )


# ---------- GET /transfers/{id} ----------

@router.get(
    "/{transfer_id}",
    response_model=TransferResponse,
    summary="Retrieve a transfer",
)
def get_transfer(
    transfer_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
) -> TransferResponse:
    transfer = db.get(Transfer, transfer_id)
    if not transfer:
        raise _error(
            request,
            status.HTTP_404_NOT_FOUND,
            "transfer_not_found",
            "Transfer not found",
        )
    return TransferResponse.from_model(transfer)


# ---------- GET /transfers ----------

@router.get(
    "",
    response_model=TransferListResponse,
    summary="List transfers",
)
def list_transfers(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    starting_after: Annotated[str | None, Query()] = None,
    account_id: Annotated[str | None, Query()] = None,
    transfer_status: Annotated[str | None, Query(alias="status")] = None,
) -> TransferListResponse:
    stmt = select(Transfer).order_by(Transfer.id.desc())

    if starting_after:
        stmt = stmt.where(Transfer.id < starting_after)
    if account_id:
        stmt = stmt.where(
            (Transfer.source_account_id == account_id)
            | (Transfer.destination_account_id == account_id)
        )
    if transfer_status:
        stmt = stmt.where(Transfer.status == transfer_status)

    stmt = stmt.limit(limit + 1)
    rows = list(db.execute(stmt).scalars())
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    return TransferListResponse(
        data=[TransferResponse.from_model(r) for r in rows],
        has_more=has_more,
        next_cursor=rows[-1].id if has_more and rows else None,
    )
