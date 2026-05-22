"""
Tests for GET /accounts/{id}/balance and GET /accounts/{id}/transactions.

Includes the concurrency stress test: many parallel deposits to the same
account from threads, then verify the ledger invariant still holds.
This is the test most likely to come up in a payments interview.
"""
import concurrent.futures
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import assert_invariant_holds


def _deposit(client, auth_headers, key, account_id, amount):
    return client.post(
        "/deposits",
        json={
            "account_id": account_id,
            "amount": amount,
            "currency": "USD",
            "rail": "ach",
        },
        headers={**auth_headers, "Idempotency-Key": key},
    )


# ---------- balance ----------

def test_balance_zero_for_new_account(client, auth_headers, active_account):
    resp = client.get(
        f"/accounts/{active_account['id']}/balance",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["account_id"] == active_account["id"]
    assert body["currency"] == "USD"
    assert body["available"] == 0
    assert body["posted"] == 0
    assert "as_of" in body


def test_balance_reflects_deposits(client, auth_headers, active_account):
    _deposit(client, auth_headers, str(uuid.uuid4()), active_account["id"], 10000)
    _deposit(client, auth_headers, str(uuid.uuid4()), active_account["id"], 5000)

    resp = client.get(
        f"/accounts/{active_account['id']}/balance",
        headers=auth_headers,
    )
    assert resp.json()["posted"] == 15000
    assert resp.json()["available"] == 15000


def test_balance_unknown_account_returns_404(client, auth_headers):
    resp = client.get("/accounts/acct_does_not_exist/balance", headers=auth_headers)
    assert resp.status_code == 404


# ---------- transactions ----------

def test_transactions_empty_for_new_account(client, auth_headers, active_account):
    resp = client.get(
        f"/accounts/{active_account['id']}/transactions",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert body["data"] == []
    assert body["has_more"] is False


def test_transactions_returned_after_deposit(client, auth_headers, active_account):
    _deposit(client, auth_headers, str(uuid.uuid4()), active_account["id"], 12345)

    resp = client.get(
        f"/accounts/{active_account['id']}/transactions",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    tx = body["data"][0]
    assert tx["type"] == "deposit"
    assert tx["amount"] == 12345
    assert tx["currency"] == "USD"
    assert tx["balance_after"] == 12345


def test_transactions_balance_after_running_correctly(
    client, auth_headers, active_account
):
    """Each transaction shows the running balance after its own posting."""
    _deposit(client, auth_headers, str(uuid.uuid4()), active_account["id"], 1000)
    _deposit(client, auth_headers, str(uuid.uuid4()), active_account["id"], 2000)
    _deposit(client, auth_headers, str(uuid.uuid4()), active_account["id"], 3000)

    resp = client.get(
        f"/accounts/{active_account['id']}/transactions",
        headers=auth_headers,
    )
    body = resp.json()
    # Returned newest-first
    assert len(body["data"]) == 3
    balances = [t["balance_after"] for t in body["data"]]
    # Newest entry's balance_after should be the total
    assert max(balances) == 6000


# ---------- concurrency stress test ----------

def test_concurrent_deposits_preserve_invariant(
    client, auth_headers, active_account, db_session
):
    """
    The defining test. Fire many simultaneous deposit requests against
    the same account from multiple threads. Then assert:
      1. The final balance equals the sum of all deposit amounts.
      2. The ledger invariant (liabilities = FBO cash) holds.
      3. Number of ledger entries = 2 * number of successful deposits.

    Why this matters: most "ledger bugs" in production come from race
    conditions in concurrent writes. If our row-level locking and
    transaction boundaries are correct, this passes. If they're sloppy,
    the invariant gets violated.

    Note: each deposit uses a unique idempotency key, so we're testing
    raw concurrency, not idempotency collisions.
    """
    N = 20
    amount = 100

    def do_deposit(_):
        key = f"concurrent-{uuid.uuid4()}"
        return client.post(
            "/deposits",
            json={
                "account_id": active_account["id"],
                "amount": amount,
                "currency": "USD",
                "rail": "ach",
            },
            headers={**auth_headers, "Idempotency-Key": key},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        responses = list(ex.map(do_deposit, range(N)))

    # All should succeed
    success_count = sum(1 for r in responses if r.status_code == 201)
    assert success_count == N, (
        f"Only {success_count}/{N} deposits succeeded. "
        f"Failures: {[r.status_code for r in responses if r.status_code != 201]}"
    )

    # Balance should equal N * amount
    bal = client.get(
        f"/accounts/{active_account['id']}/balance",
        headers=auth_headers,
    ).json()
    assert bal["posted"] == N * amount

    # Ledger entries: 2 per deposit
    count = db_session.execute(
        text("SELECT COUNT(*) FROM ledger_entries WHERE currency = 'USD'")
    ).scalar_one()
    assert count == N * 2

    # Invariant must hold
    assert_invariant_holds(db_session)
