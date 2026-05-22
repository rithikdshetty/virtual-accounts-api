"""
Test fixtures.

Key design decisions:
1. The test client is configured so each HTTP request gets its OWN database
   session, bound to the test database engine. This matches production
   behavior where every request has its own session. Without this, the
   concurrency tests would fail because SQLAlchemy sessions are not
   thread-safe.

2. The `db_session` fixture provides a separate session for tests that
   need to inspect or set up DB state directly (without going through HTTP).

3. Between tests, we truncate all tables and re-seed the FBO cash row.

4. Why truncate-between-tests instead of transactional rollback: rollback
   breaks if code under test uses nested transactions. We avoid it.
"""
import uuid
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

test_engine = create_engine(
    settings.test_database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=10,         # higher pool for concurrent tests
    max_overflow=20,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


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


def _truncate_and_reseed(db: Session) -> None:
    """Wipe data between tests; re-seed FBO row."""
    for table in TABLES_TO_TRUNCATE:
        db.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
    db.execute(
        text(
            """
            INSERT INTO accounts (
                id, customer_id, currency, status, livemode, metadata_json,
                created_at, updated_at
            )
            VALUES (
                'fbo_cash_USD', 'internal', 'USD', 'active', false,
                '{"description": "Master FBO cash account for USD"}',
                NOW(), NOW()
            )
            ON CONFLICT (id) DO NOTHING
            """
        )
    )
    db.commit()


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """
    A session for the TEST to use directly (e.g. for invariant checks
    or DB inspection). NOT shared with the HTTP client; the client uses
    its own per-request sessions.
    """
    db = TestSessionLocal()
    _truncate_and_reseed(db)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """
    FastAPI test client. Each HTTP request through the client gets a
    FRESH session from the test engine, matching production behavior.
    The db_session fixture is required as a dependency so truncation
    happens before the test client is built (otherwise the client may
    see stale state).
    """

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.api_key}"}


@pytest.fixture
def active_account(client, auth_headers):
    created = client.post(
        "/accounts",
        json={"customer_id": "cus_test", "currency": "USD"},
        headers=auth_headers,
    ).json()
    client.patch(
        f"/accounts/{created['id']}",
        json={"status": "active"},
        headers=auth_headers,
    )
    return created


@pytest.fixture
def frozen_account(client, auth_headers):
    created = client.post(
        "/accounts",
        json={"customer_id": "cus_frozen", "currency": "USD"},
        headers=auth_headers,
    ).json()
    client.patch(
        f"/accounts/{created['id']}",
        json={"status": "active"},
        headers=auth_headers,
    )
    client.patch(
        f"/accounts/{created['id']}",
        json={"status": "frozen"},
        headers=auth_headers,
    )
    return created


@pytest.fixture
def fresh_idem_key():
    return f"test-{uuid.uuid4()}"


def assert_invariant_holds(db: Session, currency: str = "USD") -> None:
    """
    The defining invariant: sum of customer liabilities equals sum of
    FBO cash entries for the same currency.
    """
    result = db.execute(
        text(
            """
            SELECT
              (SELECT COALESCE(SUM(amount), 0) FROM ledger_entries
               WHERE account_id LIKE 'acct_%'
                 AND currency = :ccy) AS liabilities,
              (SELECT COALESCE(SUM(amount), 0) FROM ledger_entries
               WHERE account_id = :fbo
                 AND currency = :ccy) AS fbo_cash
            """
        ),
        {"ccy": currency, "fbo": f"fbo_cash_{currency}"},
    ).first()
    assert result.liabilities == result.fbo_cash, (
        f"INVARIANT VIOLATED: liabilities={result.liabilities} "
        f"!= fbo_cash={result.fbo_cash}"
    )
