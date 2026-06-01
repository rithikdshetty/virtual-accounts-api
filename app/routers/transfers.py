"""
Transfers endpoint. Moves money atomically between virtual accounts.

Emits `transfer.posted` events for successful transfers and reversals.
Failed transfers do NOT emit events (they never happened from the
ledger's perspective).
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
    return db.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.account_id == account_id
        )
    ).scalar_one()


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
    if idem.cached_response:
        return Response(
            content=json.dumps(idem.cached_response, default=str),
            status_code=idem.cached_status,
            media_type="application/json",
        )

    if body.source_account_id == body.destination_account_id:
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "same_account",
            "Source and destination must differ",
        )

    source_stmt = select(Account).where(Account.id == body.source_account_id).with_for_update()
    source = db.execute(source_stmt).scalar_one_or_none()
    if not source:
        raise _error(
            request, status.HTTP_404_NOT_FOUND, "account_not_found",
            "Source account not found",
        )

    destination_stmt = select(Account).where(Account.id == body.destination_account_id).with_for_update()
    destination = db.execute(destination_stmt).scalar_one_or_none()
    if not destination:
        raise _error(
            request, status.HTTP_404_NOT_FOUND, "account_not_found",
            "Destination account not found",
        )

    if source.status != "active":
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY,
            "account_frozen" if source.status == "frozen" else "account_closed",
            f"Source account is {source.status}",
        )
    if destination.status != "active":
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY,
            "account_frozen" if destination.status == "frozen" else "account_closed",
            f"Destination account is {destination.status}",
        )

    if source.currency != destination.currency:
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "currency_mismatch",
            "Source and destination currencies differ",
            f"Source is {source.currency}, destination is {destination.currency}.",
        )

    current_balance = _compute_balance(db, body.source_account_id)
    if current_balance < body.amount:
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "insufficient_funds",
            "Insufficient funds",
            f"Source balance is {current_balance}; transfer requested {body.amount}.",
        )

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

    emit_event(db, "transfer.posted", response_body)
    idem.store(db, response_body, status_code=201)

    db.commit()

    return Response(
        content=json.dumps(response_body, default=str),
        status_code=201,
        media_type="application/json",
    )


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
    if idem.cached_response:
        return Response(
            content=json.dumps(idem.cached_response, default=str),
            status_code=idem.cached_status,
            media_type="application/json",
        )

    original_stmt = select(Transfer).where(Transfer.id == transfer_id).with_for_update()
    original = db.execute(original_stmt).scalar_one_or_none()
    if not original:
        raise _error(
            request, status.HTTP_404_NOT_FOUND, "transfer_not_found", "Transfer not found",
        )

    if original.reverses_transfer_id is not None:
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "cannot_reverse_reversal",
            "Cannot reverse a reversal",
        )

    if original.status != "posted":
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "transfer_not_posted",
            f"Transfer is {original.status}, not posted",
        )

    src_for_reversal = db.execute(
        select(Account).where(Account.id == original.destination_account_id).with_for_update()
    ).scalar_one_or_none()
    dst_for_reversal = db.execute(
        select(Account).where(Account.id == original.source_account_id).with_for_update()
    ).scalar_one_or_none()

    if not src_for_reversal or not dst_for_reversal:
        raise _error(
            request, status.HTTP_404_NOT_FOUND, "account_not_found",
            "One or both accounts no longer exist",
        )

    if dst_for_reversal.status == "closed":
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "account_closed",
            "Original source account is closed",
        )

    if src_for_reversal.status == "closed":
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "account_closed",
            "Cannot reverse; original destination account is closed",
        )

    current_balance = _compute_balance(db, src_for_reversal.id)
    if current_balance < original.amount:
        raise _error(
            request, status.HTTP_422_UNPROCESSABLE_ENTITY, "insufficient_funds",
            "Insufficient funds to reverse",
            f"Destination of original has balance {current_balance}; need {original.amount}.",
        )

    reversal_id = new_id("tfr")
    now = datetime.now(timezone.utc)

    reversal = Transfer(
        id=reversal_id,
        source_account_id=original.destination_account_id,
        destination_account_id=original.source_account_id,
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

    original.status = "reversed"

    db.add_all([reversal, rev_source_entry, rev_destination_entry])
    db.flush()

    response_body = TransferResponse.from_model(reversal).model_dump(mode="json")

    emit_event(db, "transfer.posted", response_body)
    idem.store(db, response_body, status_code=201)

    db.commit()

    return Response(
        content=json.dumps(response_body, default=str),
        status_code=201,
        media_type="application/json",
    )


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
            request, status.HTTP_404_NOT_FOUND, "transfer_not_found", "Transfer not found",
        )
    return TransferResponse.from_model(transfer)


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
