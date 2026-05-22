"""
Idempotency support for POST endpoints. The pattern:

1. Client sends POST with `Idempotency-Key: abc-123` header.
2. Server hashes the request body (canonical JSON).
3. Server looks up (api_key_hash, idempotency_key) in the cache.
4. If found and request_hash matches: return cached response (replay).
5. If found and request_hash differs: return 409 (key reused, different body).
6. If not found: execute the handler, cache the result, return it.

Why we hash the body: clients sometimes retry with slightly different
bodies. If we returned the cached response for those, we'd silently
swallow the differences. Better to detect and 409.

Why we don't use a middleware: idempotency must be scoped per endpoint.
POST /transfers and POST /deposits with the same key should NOT collide.
A per-endpoint dependency ties the key to a specific operation.

Why this dependency is async: we need to read the raw request body to
hash it, which requires `await request.body()`. Sync dependencies can't
await, and trying to spin up an event loop in a sync context (as an
earlier version did) breaks under FastAPI's threadpool model. Making
this async sidesteps all of that.

Limitations of this v0.1 implementation:
- Not atomic across concurrent requests with the same key. Two
  simultaneous POSTs with the same key may both execute. Production
  would add a SELECT ... FOR UPDATE or a Postgres advisory lock keyed
  on the idempotency key. See NOTES.md.
- Stores the full response body. For very large responses this is
  wasteful. v1.0 would store a hash plus a separate compact form.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.lib.auth import require_api_key
from app.models import IdempotencyKey


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON for hashing: sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def hash_request_body(body: dict) -> str:
    """SHA-256 of the canonical request body."""
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


class IdempotencyContext:
    """
    Carries everything an endpoint needs to handle idempotency.

    Usage in an endpoint:
        if idem.cached_response:
            return Response(content=..., status_code=idem.cached_status)
        # ... do the work ...
        idem.store(db, response_dict, status_code=201)
        return response_dict
    """

    def __init__(
        self,
        key: str,
        api_key_hash: str,
        cached_response: dict | None,
        cached_status: int | None,
        request_body: dict,
    ):
        self.key = key
        self.api_key_hash = api_key_hash
        self.cached_response = cached_response
        self.cached_status = cached_status
        self.request_body = request_body

    def store(self, db: Session, response_body: dict, status_code: int) -> None:
        """Cache the response for future replays. Caller commits."""
        entry = IdempotencyKey(
            api_key_hash=self.api_key_hash,
            idempotency_key=self.key,
            request_hash=hash_request_body(self.request_body),
            response_status=status_code,
            response_body=response_body,
        )
        db.add(entry)


async def require_idempotency_key(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    api_key_hash: Annotated[str, Depends(require_api_key)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> IdempotencyContext:
    """
    Async FastAPI dependency. Reads and hashes the request body, looks
    up the cache, returns a context the endpoint uses.
    """
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "type": "https://api.example.com/errors/missing_idempotency_key",
                "title": "Idempotency-Key header is required",
                "status": 400,
                "code": "missing_idempotency_key",
            },
        )

    if len(idempotency_key) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "type": "https://api.example.com/errors/invalid_idempotency_key",
                "title": "Idempotency-Key must be 1-255 characters",
                "status": 400,
                "code": "invalid_idempotency_key",
            },
        )

    # Read raw body for hashing. FastAPI caches it so the route handler
    # can parse it again without us interfering.
    raw_body = await request.body()
    try:
        request_body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        request_body = {}

    # Look up cached response
    cached = (
        db.execute(
            select(IdempotencyKey).where(
                and_(
                    IdempotencyKey.api_key_hash == api_key_hash,
                    IdempotencyKey.idempotency_key == idempotency_key,
                )
            )
        )
        .scalars()
        .first()
    )

    if cached and cached.expires_at < datetime.now(timezone.utc):
        # Expired; clean up so we don't keep tripping on this row.
        db.delete(cached)
        db.commit()
        cached = None

    cached_response = None
    cached_status = None

    if cached:
        new_hash = hash_request_body(request_body)
        if cached.request_hash != new_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "type": "https://api.example.com/errors/idempotency_conflict",
                    "title": "Idempotency-Key reused with a different body",
                    "status": 409,
                    "code": "idempotency_conflict",
                    "detail": (
                        "An idempotency key may only be reused with an "
                        "identical request body within 24 hours."
                    ),
                },
            )
        cached_response = cached.response_body
        cached_status = cached.response_status

    return IdempotencyContext(
        key=idempotency_key,
        api_key_hash=api_key_hash,
        cached_response=cached_response,
        cached_status=cached_status,
        request_body=request_body,
    )
