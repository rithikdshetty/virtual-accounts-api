"""
Tests for the idempotency middleware as exercised through POST /deposits.

Patterns covered:
- Missing Idempotency-Key header → 400
- Same key + same body → cached response, no new ledger entries
- Same key + different body → 409 conflict
- Different keys → independent operations
"""
from sqlalchemy import text


def _deposit(client, auth_headers, key, body):
    return client.post(
        "/deposits",
        json=body,
        headers={**auth_headers, "Idempotency-Key": key},
    )


# ---------- missing key ----------

def test_deposit_without_idempotency_key_returns_400(
    client, auth_headers, active_account
):
    """Missing Idempotency-Key header is rejected."""
    resp = client.post(
        "/deposits",
        json={
            "account_id": active_account["id"],
            "amount": 1000,
            "currency": "USD",
            "rail": "ach",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "missing_idempotency_key"


# ---------- replay ----------

def test_idempotency_replay_returns_same_response(
    client, auth_headers, active_account, fresh_idem_key
):
    """Same key + same body → cached response, identical to original."""
    body = {
        "account_id": active_account["id"],
        "amount": 7500,
        "currency": "USD",
        "rail": "wire",
        "source_reference": "REF-replay",
    }

    first = _deposit(client, auth_headers, fresh_idem_key, body)
    second = _deposit(client, auth_headers, fresh_idem_key, body)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_idempotency_replay_does_not_create_new_ledger_entries(
    client, auth_headers, active_account, fresh_idem_key, db_session
):
    """Replays must not insert duplicate ledger entries."""
    body = {
        "account_id": active_account["id"],
        "amount": 7500,
        "currency": "USD",
        "rail": "wire",
    }

    _deposit(client, auth_headers, fresh_idem_key, body)
    _deposit(client, auth_headers, fresh_idem_key, body)
    _deposit(client, auth_headers, fresh_idem_key, body)  # 3 attempts

    count = db_session.execute(
        text("SELECT COUNT(*) FROM ledger_entries WHERE currency = 'USD'")
    ).scalar_one()
    assert count == 2  # still just one deposit = 2 entries


# ---------- conflict ----------

def test_idempotency_conflict_returns_409(
    client, auth_headers, active_account, fresh_idem_key
):
    """Same key + DIFFERENT body → 409."""
    body_1 = {
        "account_id": active_account["id"],
        "amount": 1000,
        "currency": "USD",
        "rail": "ach",
    }
    body_2 = {
        "account_id": active_account["id"],
        "amount": 2000,  # different amount
        "currency": "USD",
        "rail": "ach",
    }

    first = _deposit(client, auth_headers, fresh_idem_key, body_1)
    second = _deposit(client, auth_headers, fresh_idem_key, body_2)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "idempotency_conflict"


# ---------- different keys ----------

def test_different_idempotency_keys_create_independent_deposits(
    client, auth_headers, active_account
):
    """Two different keys → two independent operations."""
    body = {
        "account_id": active_account["id"],
        "amount": 1000,
        "currency": "USD",
        "rail": "ach",
    }
    first = _deposit(client, auth_headers, "key-001", body)
    second = _deposit(client, auth_headers, "key-002", body)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


# ---------- canonical hashing ----------

def test_idempotency_same_body_different_key_order_is_replay(
    client, auth_headers, active_account, fresh_idem_key
):
    """
    JSON with same keys in different order should hash to the same value
    via canonical JSON. So two bodies with the same content but different
    key ordering should be treated as identical replays.
    """
    body_1 = {
        "account_id": active_account["id"],
        "amount": 1000,
        "currency": "USD",
        "rail": "ach",
    }
    body_2 = {  # same fields, different order
        "rail": "ach",
        "amount": 1000,
        "account_id": active_account["id"],
        "currency": "USD",
    }
    first = _deposit(client, auth_headers, fresh_idem_key, body_1)
    second = _deposit(client, auth_headers, fresh_idem_key, body_2)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
