"""
Tests for POST /withdrawals and read endpoints.
Withdrawals are the mirror of deposits: both ledger entries negative,
FBO cash decreases alongside customer balance.
"""
import uuid

from sqlalchemy import text

from tests.conftest import assert_invariant_holds


def _create_active_account(client, auth_headers, customer_id="cus_w"):
    created = client.post(
        "/accounts",
        json={"customer_id": customer_id, "currency": "USD"},
        headers=auth_headers,
    ).json()
    client.patch(
        f"/accounts/{created['id']}",
        json={"status": "active"},
        headers=auth_headers,
    )
    return created


def _fund(client, auth_headers, account_id, amount):
    return client.post(
        "/deposits",
        json={"account_id": account_id, "amount": amount, "currency": "USD", "rail": "wire"},
        headers={**auth_headers, "Idempotency-Key": f"fund-{uuid.uuid4()}"},
    )


def _withdraw(client, auth_headers, key, account_id, amount, **extra):
    body = {
        "account_id": account_id,
        "amount": amount,
        "currency": "USD",
        "rail": "ach",
        "destination_reference": "ext-ref-001",
        **extra,
    }
    return client.post(
        "/withdrawals",
        json=body,
        headers={**auth_headers, "Idempotency-Key": key},
    )


def _balance(client, auth_headers, account_id):
    return client.get(
        f"/accounts/{account_id}/balance", headers=auth_headers
    ).json()["posted"]


# ---------- happy path ----------

def test_withdrawal_succeeds(client, auth_headers, db_session):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 10000)

    resp = _withdraw(client, auth_headers, str(uuid.uuid4()), a["id"], 3000)
    assert resp.status_code == 201
    body = resp.json()
    assert body["object"] == "withdrawal"
    assert body["id"].startswith("wdl_")
    assert body["amount"] == 3000
    assert body["status"] == "posted"
    assert body["destination_reference"] == "ext-ref-001"

    assert _balance(client, auth_headers, a["id"]) == 7000
    assert_invariant_holds(db_session)


def test_withdrawal_decreases_fbo_cash(client, auth_headers, db_session):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 10000)

    fbo_before = db_session.execute(
        text("SELECT COALESCE(SUM(amount),0) FROM ledger_entries WHERE account_id = 'fbo_cash_USD'")
    ).scalar_one()

    _withdraw(client, auth_headers, str(uuid.uuid4()), a["id"], 3000)

    fbo_after = db_session.execute(
        text("SELECT COALESCE(SUM(amount),0) FROM ledger_entries WHERE account_id = 'fbo_cash_USD'")
    ).scalar_one()

    assert fbo_after == fbo_before - 3000
    assert_invariant_holds(db_session)


def test_withdrawal_creates_paired_entries(client, auth_headers, db_session):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 10000)

    before = db_session.execute(
        text("SELECT COUNT(*) FROM ledger_entries WHERE related_withdrawal_id IS NOT NULL")
    ).scalar_one()
    _withdraw(client, auth_headers, str(uuid.uuid4()), a["id"], 2000)
    after = db_session.execute(
        text("SELECT COUNT(*) FROM ledger_entries WHERE related_withdrawal_id IS NOT NULL")
    ).scalar_one()
    assert after - before == 2


# ---------- failure modes ----------

def test_withdrawal_insufficient_funds(client, auth_headers, db_session):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 1000)

    resp = _withdraw(client, auth_headers, str(uuid.uuid4()), a["id"], 5000)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "insufficient_funds"
    assert _balance(client, auth_headers, a["id"]) == 1000
    assert_invariant_holds(db_session)


def test_withdrawal_unknown_account_404(client, auth_headers):
    resp = _withdraw(client, auth_headers, str(uuid.uuid4()), "acct_nope", 1000)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "account_not_found"


def test_withdrawal_frozen_account_422(client, auth_headers):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 5000)
    client.patch(
        f"/accounts/{a['id']}", json={"status": "frozen"}, headers=auth_headers
    )
    resp = _withdraw(client, auth_headers, str(uuid.uuid4()), a["id"], 1000)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "account_frozen"


def test_withdrawal_currency_mismatch_422(client, auth_headers):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 5000)
    resp = _withdraw(
        client, auth_headers, str(uuid.uuid4()), a["id"], 1000, currency="EUR"
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "currency_mismatch"


# ---------- idempotency ----------

def test_withdrawal_idempotency_replay(client, auth_headers):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 10000)
    key = str(uuid.uuid4())
    first = _withdraw(client, auth_headers, key, a["id"], 2000).json()
    second = _withdraw(client, auth_headers, key, a["id"], 2000).json()
    assert first["id"] == second["id"]


def test_withdrawal_idempotency_no_duplicate_entries(
    client, auth_headers, db_session
):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 10000)
    key = str(uuid.uuid4())
    _withdraw(client, auth_headers, key, a["id"], 2000)
    _withdraw(client, auth_headers, key, a["id"], 2000)

    count = db_session.execute(
        text("SELECT COUNT(*) FROM ledger_entries WHERE related_withdrawal_id IS NOT NULL")
    ).scalar_one()
    assert count == 2  # one withdrawal, not two
    # balance reflects one withdrawal
    assert _balance(client, auth_headers, a["id"]) == 8000


# ---------- event ----------

def test_withdrawal_emits_event(client, auth_headers, db_session):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 5000)
    _withdraw(client, auth_headers, str(uuid.uuid4()), a["id"], 1000)

    count = db_session.execute(
        text("SELECT COUNT(*) FROM events WHERE event_type = 'withdrawal.posted'")
    ).scalar_one()
    assert count == 1


# ---------- read ----------

def test_get_withdrawal(client, auth_headers):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 5000)
    w = _withdraw(client, auth_headers, str(uuid.uuid4()), a["id"], 1000).json()
    resp = client.get(f"/withdrawals/{w['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == w["id"]


def test_list_withdrawals_by_account(client, auth_headers):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 10000)
    _withdraw(client, auth_headers, str(uuid.uuid4()), a["id"], 1000)
    _withdraw(client, auth_headers, str(uuid.uuid4()), a["id"], 2000)

    resp = client.get(f"/withdrawals?account_id={a['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 2
