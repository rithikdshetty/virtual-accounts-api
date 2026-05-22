"""
Test fixtures.

Two important things this file does:
1. Overrides the FastAPI `get_db` dependency to point at the test database.
2. Truncates all tables between tests for isolation. Each test gets a
   clean slate.

Why truncate-between-tests instead of transactional rollback: the latter
is faster but it breaks if the code under test uses nested transactions
(SAVEPOINT). Our transfer logic will use nested transactions for
idempotency, so we go with truncate.
"""
import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import get_db
from app.main import app


if not settings.test_database_url:
    raise RuntimeError(
        "TEST_DATABASE_URL is not set. Add it to .env before running tests."
    )

# Separate engine pointed at the test database.
test_engine = create_engine(
    settings.test_database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


# Tables in the order they should be truncated. Children before parents
# would matter if we had FK constraints; we don't, so order is flexible.
TABLES_TO_TRUNCATE = [
    "webhook_deliveries",
    "events",
    "webhook_endpoints",
    "idempotency_keys",
    "withdrawals",
    "deposits",
    "transfers",
    "ledger_entries",
    "accounts",
]


def _truncate_all(db: Session) -> None:
    """Wipe data between tests. Schema stays."""
    for table in TABLES_TO_TRUNCATE:
        db.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
    db.commit()


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """A SQLAlchemy session bound to the test DB. Truncated before each test."""
    db = TestSessionLocal()
    _truncate_all(db)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """
    FastAPI test client. Overrides the get_db dependency so every endpoint
    uses our test session instead of the real one.
    """

    def override_get_db():
        try:
            yield db_session
        finally:
            pass  # session lifecycle managed by db_session fixture

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Pre-built auth headers for authenticated requests."""
    return {"Authorization": f"Bearer {settings.api_key}"}
