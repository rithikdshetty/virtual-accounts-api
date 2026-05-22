"""
Account endpoints. Implements the accounts portion of the OpenAPI spec.

Behaviors:
- POST /accounts: creates account in `pending` state with zero balance.
- GET /accounts: lists with cursor pagination (limit, starting_after).
- GET /accounts/{id}: retrieves single account.
- PATCH /accounts/{id}: mutates status and/or metadata.
  Status transitions are constrained: closed is terminal; balance
  must be zero to close (deferred: needs the ledger, added in Phase 4).
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.lib.auth import require_api_key
from app.lib.ids import new_id
from app.models import Account
from app.schemas.account import (
    AccountCreateRequest,
    AccountListResponse,
    AccountResponse,
    AccountUpdateRequest,
)
from app.config import settings

router = APIRouter(prefix="/accounts", tags=["Accounts"])


def _error(
    request: Request,
    status_code: int,
    code: str,
    title: str,
    detail: str | None = None,
) -> HTTPException:
    """Build an HTTPException with the RFC 7807 shape consistently."""
    request_id = getattr(request.state, "request_id", None)
    return HTTPException(
        status_code=status_code,
        detail={
            "type": f"https://api.example.com/errors/{code}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "code": code,
            "request_id": request_id,
        },
    )


@router.post(
    "",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a virtual account",
)
def create_account(
    body: AccountCreateRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
) -> AccountResponse:
    """
    Creates a new account in pending status. Returns 201 with the full
    resource on success.

    Note: idempotency middleware will be added in Phase 4. For now,
    duplicate POSTs create duplicate accounts.
    """
    account = Account(
        id=new_id("acct"),
        customer_id=body.customer_id,
        currency=body.currency,
        status="pending",
        livemode=settings.livemode,
        metadata_json=body.metadata,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return AccountResponse.from_model(account)


@router.get(
    "",
    response_model=AccountListResponse,
    summary="List virtual accounts",
)
def list_accounts(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    starting_after: Annotated[str | None, Query()] = None,
    customer_id: Annotated[str | None, Query()] = None,
    account_status: Annotated[str | None, Query(alias="status")] = None,
) -> AccountListResponse:
    """
    Cursor pagination. Pass `starting_after=<last_id_from_previous_page>`
    to get the next page. Returns up to `limit` results.

    We fetch limit+1 rows to compute has_more without a separate count
    query (count(*) on a paginated query is O(n) and pointless).
    """
    stmt = select(Account).order_by(Account.id)

    if starting_after:
        stmt = stmt.where(Account.id > starting_after)
    if customer_id:
        stmt = stmt.where(Account.customer_id == customer_id)
    if account_status:
        stmt = stmt.where(Account.status == account_status)

    stmt = stmt.limit(limit + 1)
    rows = list(db.execute(stmt).scalars())

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    return AccountListResponse(
        data=[AccountResponse.from_model(r) for r in rows],
        has_more=has_more,
        next_cursor=rows[-1].id if has_more and rows else None,
    )


@router.get(
    "/{account_id}",
    response_model=AccountResponse,
    summary="Retrieve a virtual account",
)
def get_account(
    account_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
) -> AccountResponse:
    account = db.get(Account, account_id)
    if not account:
        raise _error(
            request,
            status.HTTP_404_NOT_FOUND,
            "account_not_found",
            "Account not found",
            f"No account with id={account_id}",
        )
    return AccountResponse.from_model(account)


@router.patch(
    "/{account_id}",
    response_model=AccountResponse,
    summary="Update a virtual account",
)
def update_account(
    account_id: str,
    body: AccountUpdateRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
) -> AccountResponse:
    """
    Mutates status and/or metadata. Status transition rules:
    - pending  -> active | closed   (closed only if no activity yet)
    - active   <-> frozen
    - active   -> closed   (Phase 4: requires zero balance)
    - frozen   -> closed   (Phase 4: requires zero balance)
    - closed   -> *        REJECTED (terminal)

    Phase 3 implements the state machine but defers the zero-balance check
    to Phase 4 when the ledger exists.
    """
    account = db.get(Account, account_id)
    if not account:
        raise _error(
            request,
            status.HTTP_404_NOT_FOUND,
            "account_not_found",
            "Account not found",
        )

    if body.status is not None and body.status != account.status:
        if account.status == "closed":
            raise _error(
                request,
                status.HTTP_409_CONFLICT,
                "invalid_status_transition",
                "Invalid status transition",
                f"Account is closed (terminal); cannot transition to {body.status}",
            )
        valid_transitions = {
            "pending": {"active", "closed"},
            "active": {"frozen", "closed"},
            "frozen": {"active", "closed"},
        }
        if body.status not in valid_transitions.get(account.status, set()):
            raise _error(
                request,
                status.HTTP_409_CONFLICT,
                "invalid_status_transition",
                "Invalid status transition",
                f"Cannot transition {account.status} -> {body.status}",
            )
        account.status = body.status

    if body.metadata is not None:
        account.metadata_json = body.metadata

    from datetime import datetime, timezone

    account.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(account)
    return AccountResponse.from_model(account)
