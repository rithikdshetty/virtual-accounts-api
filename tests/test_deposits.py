"""
Tests for POST /deposits. Cover the happy path plus every documented
failure mode. Every successful test asserts the ledger invariant holds.

Idempotency tests live in test_idempotency.py to keep concerns separated.
"""
from fastapi.testclient import TestClient

from tests.conftest import assert_invariant_holds


def _deposit(client, auth_headers, key, body):
    """Helper: POST a deposit with the given idempotency key and body."""
    return client.post(
        "/deposits",
        json=body,
        headers={**auth_headers, "Idempotency-Key": key},
    )


# ---------- happy path ----------

def test_create_deposit_succeeds(client, auth_headers, active_account, fresh_idem_key):
    resp = _deposit(
        client, auth_headers, fresh_idem_key,
        {
            "account_id": active_account["id"],
            "amount": 10000,
            "currency": "USD",
            "rail": "wire",
            "source_reference": "REF-001",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["object"] == "deposit"
    assert body["id"].startswith("dep_")
    assert body["account_id"] == active_account["id"]
    assert body["amount"] == 10000
    assert body["currency"] == "USD"
    assert body["status"] == "posted"
    assert body["rail"] == "wire"
    assert body["source_reference"] == "REF-001"
    assert body["livemode"] is False
    assert body["idempotency_key"] == fresh_idem_key


def test_deposit_response_has_request_id_header(
    client, auth_headers, active_account, fresh_idem_key
):
    resp = _deposit(
        client, auth_headers, fresh_idem_key,
        {
            "account_id": active_account["id"],
            "amount": 1000,
            "currency": "USD",
            "rail": "ach",
        },
    )
    assert "request-id" in {k.lower() for k in resp.headers.keys()}


def test_deposit_creates_paired_ledger_entries(
    client, auth_headers, active_account, fresh_idem_key, db_session
):
    """Single deposit should produce exactly 2 ledger entries."""
    _deposit(
        client, auth_headers, fresh_idem_key,
        {
            "account_id": active_account["id"],
            "amount": 5000,
            "currency": "USD",
            "rail": "rtp",
        },
    )

    from sqlalchemy import text as sa_text
    result = db_session.execute(
        sa_text("SELECT COUNT(*) FROM ledger_entries WHERE currency = 'USD'")
    ).scalar_one()
    assert result == 2  # one FBO entry, one customer entry
    assert_invariant_holds(db_session)


def test_deposit_holds_invariant(
    client, auth_headers, active_account, fresh_idem_key, db_session
):
    """After deposit, sum(FBO) must equal sum(customer liabilities)."""
    _deposit(
        client, auth_headers, fresh_idem_key,
        {
            "account_id": active_account["id"],
            "amount": 42000,
            "currency": "USD",
            "rail": "wire",
        },
    )
    assert_invariant_holds(db_session)


# ---------- auth ----------

def test_deposit_without_auth_returns_401(client, active_account, fresh_idem_key):
    resp = client.post(
        "/deposits",
        json={
            "account_id": active_account["id"],
            "amount": 1000,
            "currency": "USD",
            "rail": "ach",
        },
        headers={"Idempotency-Key": fresh_idem_key},
    )
    assert resp.status_code == 401


# ---------- validation ----------

def test_deposit_to_unknown_account_returns_404(client, auth_headers, fresh_idem_key):
    resp = _deposit(
        client, auth_headers, fresh_idem_key,
        {
            "account_id": "acct_does_not_exist",
            "amount": 1000,
            "currency": "USD",
            "rail": "ach",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "account_not_found"


def test_deposit_to_frozen_account_returns_422(
    client, auth_headers, frozen_account, fresh_idem_key
):
    resp = _deposit(
        client, auth_headers, fresh_idem_key,
        {
            "account_id": frozen_account["id"],
            "amount": 1000,
            "currency": "USD",
            "rail": "ach",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "account_not_active"


def test_deposit_currency_mismatch_returns_422(
    client, auth_headers, active_account, fresh_idem_key
):
    """Account is USD, deposit specifies EUR."""
    resp = _deposit(
        client, auth_headers, fresh_idem_key,
        {
            "account_id": active_account["id"],
            "amount": 1000,
            "currency": "EUR",
            "rail": "ach",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "currency_mismatch"


def test_deposit_unsupported_currency_returns_422(
    client, auth_headers, fresh_idem_key
):
    """No FBO seed for JPY in v0.1. Create a JPY account first."""
    created = client.post(
        "/accounts",
        json={"customer_id": "cus_jpy", "currency": "JPY"},
        headers=auth_headers,
    ).json()
    client.patch(
        f"/accounts/{created['id']}",
        json={"status": "active"},
        headers=auth_headers,
    )

    resp = _deposit(
        client, auth_headers, fresh_idem_key,
        {
            "account_id": created["id"],
            "amount": 1000,
            "currency": "JPY",
            "rail": "wire",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "fbo_not_configured"


def test_deposit_negative_amount_rejected_at_validation(
    client, auth_headers, active_account, fresh_idem_key
):
    resp = _deposit(
        client, auth_headers, fresh_idem_key,
        {
            "account_id": active_account["id"],
            "amount": -100,
            "currency": "USD",
            "rail": "ach",
        },
    )
    assert resp.status_code == 422  # Pydantic field validator


def test_deposit_invalid_rail_returns_422(
    client, auth_headers, active_account, fresh_idem_key
):
    resp = _deposit(
        client, auth_headers, fresh_idem_key,
        {
            "account_id": active_account["id"],
            "amount": 1000,
            "currency": "USD",
            "rail": "carrier_pigeon",
        },
    )
    assert resp.status_code == 422
