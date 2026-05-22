"""
Bearer token authentication. v0.1 uses a single API key loaded from
config. The key is hashed at request time so downstream code (e.g.
idempotency scoping) uses the hash, not the raw key.

We use FastAPI's HTTPBearer security scheme rather than reading the
header manually. Two reasons:
1. It wires up Swagger UI's "Authorize" button automatically.
2. It generates correct OpenAPI security metadata, matching our spec's
   `securitySchemes: bearerAuth`.

v1.0 would store hashed keys in the database with per-key scopes,
expiration, and rotation history.
"""
import hashlib
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

# auto_error=False so we can return our own RFC 7807 shape instead of
# FastAPI's default {"detail": "Not authenticated"} when missing.
bearer_scheme = HTTPBearer(auto_error=False)


def hash_api_key(key: str) -> str:
    """SHA-256 hash of the API key. Stored / compared as hex."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def require_api_key(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
) -> str:
    """
    FastAPI dependency. Validates the bearer token and returns its hash
    for downstream use (e.g. idempotency scoping).

    Returns 401 with our standard error shape if missing or invalid.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "type": "https://api.example.com/errors/unauthorized",
                "title": "Missing or invalid API key",
                "status": 401,
                "code": "unauthorized",
            },
        )

    if credentials.credentials != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "type": "https://api.example.com/errors/unauthorized",
                "title": "Missing or invalid API key",
                "status": 401,
                "code": "unauthorized",
            },
        )

    return hash_api_key(credentials.credentials)
