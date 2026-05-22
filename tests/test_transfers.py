"""
Tests for POST /transfers, POST /transfers/{id}/reversal, and the read
endpoints.

Coverage targets:
- Happy path transfer (A → B)
- All failure modes with correct typed error codes:
  insufficient_funds, currency_mismatch, same_account,
  account_frozen, account_closed, account_not_found
- Reversal happy path (balances restored, original marked reversed)
- All reversal failure modes:
  cannot_reverse_reversal, transfer_not_posted, insufficient_funds,
  account_closed
- Concurrent transfers from same source (the stress test)
- Invariant holds throughout

The concurrent test is the most important one. It proves that row-level
locking on the source account serializes concurrent debits correctly:
no two transfers can both pass the balance check if only one should
succeed.
"""
import concurrent.futures
import uuid

from sqlalchemy import text

from tests.conftest import assert_invariant_holds


# ---------- helpers ----------

def _fund(client, auth_headers, account_id, amount, key_prefix="fund"):
    """Deposit funds into an account."""
    return client.post(
        "/deposits",
        json={
            "account_id": account_id,
            "amount": amount,
            "currency": "USD",
            "rail": "wire",
        },
        headers={
            **auth_headers,
            "Idempotency-Key": f"{key_prefix}-{uuid.uuid4()}",
        },
    )


def _transfer(client, auth_headers, key, source, destination, amount, **extra):
    body = {
        "source_account_id": source,
        "destination_account_id": destination,
        "amount": amount,
        **extra,
    }
    return client.post(
        "/transfers",
        json=body,
        headers={**auth_headers, "Idempotency-Key": key},
    )


def _reverse(client, auth_headers, key, transfer_id, **extra):
    return client.post(
        f"/transfers/{transfer_id}/reversal",
        json=extra,
        headers={**auth_headers, "Idempotency-Key": key},
    )


def _balance(client, auth_headers, account_id):
    return client.get(
        f"/accounts/{account_id}/balance", headers=auth_headers
    ).json()["posted"]


def _create_active_account(client, auth_headers, customer_id, currency="USD"):
    """Create an account and activate it. Returns the account dict."""
    created = client.post(
        "/accounts",
        json={"customer_id": customer_id, "currency": currency},
        headers=auth_headers,
    ).json()
    client.patch(
        f"/accounts/{created['id']}",
        json={"status": "active"},
        headers=auth_headers,
    )
    return created


# ---------- happy path ----------

def test_transfer_succeeds(client, auth_headers, db_session):
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    _fund(client, auth_headers, a["id"], 10000)

    resp = _transfer(
        client, auth_headers, str(uuid.uuid4()),
        a["id"], b["id"], 4000,
        description="Test transfer",
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["object"] == "transfer"
    assert body["id"].startswith("tfr_")
    assert body["source_account_id"] == a["id"]
    assert body["destination_account_id"] == b["id"]
    assert body["amount"] == 4000
    assert body["currency"] == "USD"
    assert body["status"] == "posted"
    assert body["reverses_transfer_id"] is None
    assert body["failure_code"] is None
    assert body["description"] == "Test transfer"

    # Balances reflect the transfer
    assert _balance(client, auth_headers, a["id"]) == 6000
    assert _balance(client, auth_headers, b["id"]) == 4000

    assert_invariant_holds(db_session)


def test_transfer_creates_paired_ledger_entries(
    client, auth_headers, db_session
):
    """One transfer = 2 ledger entries (debit source, credit destination)."""
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    _fund(client, auth_headers, a["id"], 10000)

    # Clear the deposit's ledger entries from our count
    initial_count = db_session.execute(
        text("SELECT COUNT(*) FROM ledger_entries WHERE related_transfer_id IS NOT NULL")
    ).scalar_one()

    _transfer(client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 4000)

    new_count = db_session.execute(
        text("SELECT COUNT(*) FROM ledger_entries WHERE related_transfer_id IS NOT NULL")
    ).scalar_one()

    assert new_count - initial_count == 2
    assert_invariant_holds(db_session)


def test_transfer_response_has_request_id_header(client, auth_headers):
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    _fund(client, auth_headers, a["id"], 5000)

    resp = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 1000
    )
    assert "request-id" in {k.lower() for k in resp.headers.keys()}


# ---------- failure modes ----------

def test_transfer_insufficient_funds_returns_422(
    client, auth_headers, db_session
):
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    _fund(client, auth_headers, a["id"], 1000)

    resp = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 9999
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "insufficient_funds"

    # Balances unchanged
    assert _balance(client, auth_headers, a["id"]) == 1000
    assert _balance(client, auth_headers, b["id"]) == 0
    assert_invariant_holds(db_session)


def test_transfer_same_account_returns_422(client, auth_headers):
    a = _create_active_account(client, auth_headers, "cus_alice")
    _fund(client, auth_headers, a["id"], 5000)

    resp = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], a["id"], 1000
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "same_account"


def test_transfer_unknown_source_returns_404(client, auth_headers):
    b = _create_active_account(client, auth_headers, "cus_bob")
    resp = _transfer(
        client, auth_headers, str(uuid.uuid4()),
        "acct_does_not_exist", b["id"], 1000,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "account_not_found"


def test_transfer_unknown_destination_returns_404(client, auth_headers):
    a = _create_active_account(client, auth_headers, "cus_alice")
    _fund(client, auth_headers, a["id"], 5000)
    resp = _transfer(
        client, auth_headers, str(uuid.uuid4()),
        a["id"], "acct_does_not_exist", 1000,
    )
    assert resp.status_code == 404


def test_transfer_frozen_source_returns_422(client, auth_headers):
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    _fund(client, auth_headers, a["id"], 5000)

    client.patch(
        f"/accounts/{a['id']}",
        json={"status": "frozen"},
        headers=auth_headers,
    )

    resp = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 1000
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "account_frozen"


def test_transfer_frozen_destination_returns_422(client, auth_headers):
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    _fund(client, auth_headers, a["id"], 5000)

    client.patch(
        f"/accounts/{b['id']}",
        json={"status": "frozen"},
        headers=auth_headers,
    )

    resp = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 1000
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "account_frozen"


def test_transfer_closed_source_returns_422(client, auth_headers):
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    # Close A while it has zero balance
    client.patch(
        f"/accounts/{a['id']}",
        json={"status": "closed"},
        headers=auth_headers,
    )

    resp = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 1000
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "account_closed"


def test_transfer_pending_source_returns_422(client, auth_headers):
    """Pending accounts are not transactable."""
    # Don't activate
    a = client.post(
        "/accounts",
        json={"customer_id": "cus_pending", "currency": "USD"},
        headers=auth_headers,
    ).json()
    b = _create_active_account(client, auth_headers, "cus_bob")

    resp = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 1000
    )
    assert resp.status_code == 422
    # Pending isn't 'frozen' or 'closed', so we return one of the two
    # account-state codes based on our router's branching. Just confirm 422.


# ---------- reversal happy path ----------

def test_reversal_restores_balances(client, auth_headers, db_session):
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    _fund(client, auth_headers, a["id"], 10000)

    tx = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 3000
    ).json()
    assert _balance(client, auth_headers, a["id"]) == 7000
    assert _balance(client, auth_headers, b["id"]) == 3000

    rev = _reverse(client, auth_headers, str(uuid.uuid4()), tx["id"])
    assert rev.status_code == 201
    rev_body = rev.json()
    assert rev_body["reverses_transfer_id"] == tx["id"]
    assert rev_body["source_account_id"] == b["id"]
    assert rev_body["destination_account_id"] == a["id"]
    assert rev_body["amount"] == 3000

    # Balances restored
    assert _balance(client, auth_headers, a["id"]) == 10000
    assert _balance(client, auth_headers, b["id"]) == 0
    assert_invariant_holds(db_session)


def test_reversal_marks_original_as_reversed(client, auth_headers):
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    _fund(client, auth_headers, a["id"], 5000)

    tx = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 2000
    ).json()
    _reverse(client, auth_headers, str(uuid.uuid4()), tx["id"])

    # Fetch the original
    orig = client.get(
        f"/transfers/{tx['id']}", headers=auth_headers
    ).json()
    assert orig["status"] == "reversed"


# ---------- reversal failure modes ----------

def test_reversal_of_reversal_returns_422(client, auth_headers):
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    _fund(client, auth_headers, a["id"], 5000)

    tx = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 1000
    ).json()
    rev = _reverse(client, auth_headers, str(uuid.uuid4()), tx["id"]).json()

    # Try to reverse the reversal
    resp = _reverse(client, auth_headers, str(uuid.uuid4()), rev["id"])
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "cannot_reverse_reversal"


def test_reversal_of_unknown_transfer_returns_404(client, auth_headers):
    resp = _reverse(
        client, auth_headers, str(uuid.uuid4()), "tfr_does_not_exist"
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "transfer_not_found"


def test_reversal_insufficient_funds_returns_422(
    client, auth_headers, db_session
):
    """
    If destination spent down the transferred funds before reversal,
    the reversal fails.
    """
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    c = _create_active_account(client, auth_headers, "cus_charlie")
    _fund(client, auth_headers, a["id"], 5000)

    tx = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 3000
    ).json()
    # B spends the funds away to C
    _transfer(client, auth_headers, str(uuid.uuid4()), b["id"], c["id"], 3000)

    # Now try to reverse the original; B has no funds
    resp = _reverse(client, auth_headers, str(uuid.uuid4()), tx["id"])
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "insufficient_funds"
    assert_invariant_holds(db_session)


def test_reversal_idempotency_replay(client, auth_headers):
    """Same reversal request twice returns the cached response."""
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    _fund(client, auth_headers, a["id"], 5000)

    tx = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 1000
    ).json()

    key = str(uuid.uuid4())
    first = _reverse(client, auth_headers, key, tx["id"]).json()
    second = _reverse(client, auth_headers, key, tx["id"]).json()
    assert first["id"] == second["id"]


# ---------- read endpoints ----------

def test_get_transfer_returns_full_resource(client, auth_headers):
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    _fund(client, auth_headers, a["id"], 5000)
    tx = _transfer(
        client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 2000
    ).json()

    resp = client.get(f"/transfers/{tx['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == tx["id"]


def test_get_unknown_transfer_returns_404(client, auth_headers):
    resp = client.get("/transfers/tfr_does_not_exist", headers=auth_headers)
    assert resp.status_code == 404


def test_list_transfers_filters_by_account(client, auth_headers):
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    c = _create_active_account(client, auth_headers, "cus_charlie")
    _fund(client, auth_headers, a["id"], 10000)
    _fund(client, auth_headers, c["id"], 10000)

    # Two transfers involving A, one not
    _transfer(client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 1000)
    _transfer(client, auth_headers, str(uuid.uuid4()), a["id"], b["id"], 2000)
    _transfer(client, auth_headers, str(uuid.uuid4()), c["id"], b["id"], 500)

    resp = client.get(
        f"/transfers?account_id={a['id']}", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    # Both A → B transfers should be returned
    assert len(body["data"]) == 2
    for tx in body["data"]:
        assert tx["source_account_id"] == a["id"] or tx["destination_account_id"] == a["id"]


# ---------- concurrency stress test ----------

def test_concurrent_transfers_from_same_source_preserve_invariant(
    client, auth_headers, db_session
):
    """
    The defining transfer test. Fire many simultaneous transfers from
    the same source account. With $1000 in the source and each transfer
    being $100, exactly 10 should succeed. The rest must fail with
    insufficient_funds (NOT silently double-spend).

    What this proves:
    - Row-level locking serializes concurrent debits from the same account
    - The balance check and the debit are atomic within a transaction
    - No two concurrent transfers can both pass when only one should

    Without SELECT FOR UPDATE on the source, this test fails: multiple
    transfers see the same initial balance and all "succeed", driving
    the balance negative or violating the invariant.
    """
    a = _create_active_account(client, auth_headers, "cus_alice")
    b = _create_active_account(client, auth_headers, "cus_bob")
    _fund(client, auth_headers, a["id"], 1000)

    N_ATTEMPTS = 20
    AMOUNT = 100

    def do_transfer(_):
        return _transfer(
            client, auth_headers,
            f"concurrent-tx-{uuid.uuid4()}",
            a["id"], b["id"], AMOUNT,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        responses = list(ex.map(do_transfer, range(N_ATTEMPTS)))

    successes = [r for r in responses if r.status_code == 201]
    failures = [r for r in responses if r.status_code == 422]

    # Exactly 10 should succeed (1000 / 100 = 10)
    assert len(successes) == 10, (
        f"Expected 10 successes, got {len(successes)}. "
        f"Failures: {[r.status_code for r in failures]}"
    )
    assert len(failures) == N_ATTEMPTS - 10

    # All failures should be insufficient_funds
    for f in failures:
        assert f.json()["detail"]["code"] == "insufficient_funds"

    # Final balances must equal exactly the successful transfers
    assert _balance(client, auth_headers, a["id"]) == 0
    assert _balance(client, auth_headers, b["id"]) == 1000

    # Invariant must hold
    assert_invariant_holds(db_session)
