"""
Tests for the event emission layer and webhook endpoint CRUD.
Worker tests live in test_webhook_worker.py.
"""
import uuid

from sqlalchemy import text


def _create_active_account(client, auth_headers, customer_id="cus_event"):
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
        json={
            "account_id": account_id,
            "amount": amount,
            "currency": "USD",
            "rail": "wire",
        },
        headers={**auth_headers, "Idempotency-Key": f"fund-{uuid.uuid4()}"},
    )


def _register_endpoint(client, auth_headers, event_types=None):
    return client.post(
        "/webhook_endpoints",
        json={
            "url": "http://localhost:9000/webhook",
            "event_types": event_types or ["deposit.posted", "transfer.posted"],
        },
        headers={**auth_headers, "Idempotency-Key": f"whk-{uuid.uuid4()}"},
    )


# ---------- event emission ----------

def test_deposit_creates_event_row(client, auth_headers, db_session):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 5000)

    count = db_session.execute(
        text("SELECT COUNT(*) FROM events WHERE event_type = 'deposit.posted'")
    ).scalar_one()
    assert count == 1


def test_transfer_creates_event_row(client, auth_headers, db_session):
    a = _create_active_account(client, auth_headers, "cus_a")
    b = _create_active_account(client, auth_headers, "cus_b")
    _fund(client, auth_headers, a["id"], 5000)

    client.post(
        "/transfers",
        json={
            "source_account_id": a["id"],
            "destination_account_id": b["id"],
            "amount": 1000,
        },
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    count = db_session.execute(
        text("SELECT COUNT(*) FROM events WHERE event_type = 'transfer.posted'")
    ).scalar_one()
    assert count == 1


def test_failed_deposit_does_not_create_event(client, auth_headers, db_session):
    """Validation failures don't emit events; only successful posts do."""
    resp = client.post(
        "/deposits",
        json={
            "account_id": "acct_nope",
            "amount": 100,
            "currency": "USD",
            "rail": "wire",
        },
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert resp.status_code == 404

    count = db_session.execute(text("SELECT COUNT(*) FROM events")).scalar_one()
    assert count == 0


def test_event_creates_delivery_for_subscribed_endpoint(
    client, auth_headers, db_session
):
    """Active subscribed endpoint → pending delivery row on event."""
    _register_endpoint(client, auth_headers, ["deposit.posted"])
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 5000)

    count = db_session.execute(
        text("SELECT COUNT(*) FROM webhook_deliveries WHERE status = 'pending'")
    ).scalar_one()
    assert count == 1


def test_event_does_not_create_delivery_for_unsubscribed_endpoint(
    client, auth_headers, db_session
):
    """Endpoint subscribed only to transfers; deposit shouldn't fan out."""
    _register_endpoint(client, auth_headers, ["transfer.posted"])
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 5000)

    count = db_session.execute(
        text("SELECT COUNT(*) FROM webhook_deliveries")
    ).scalar_one()
    assert count == 0


# ---------- GET /events ----------

def test_list_events_empty(client, auth_headers):
    resp = client.get("/events", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert body["data"] == []
    assert body["has_more"] is False


def test_list_events_returns_deposit_event(client, auth_headers):
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 9999)

    resp = client.get("/events", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    event = body["data"][0]
    assert event["object"] == "event"
    assert event["type"] == "deposit.posted"
    assert event["data"]["amount"] == 9999


def test_list_events_filters_by_type(client, auth_headers):
    a = _create_active_account(client, auth_headers, "cus_a")
    b = _create_active_account(client, auth_headers, "cus_b")
    _fund(client, auth_headers, a["id"], 5000)
    client.post(
        "/transfers",
        json={
            "source_account_id": a["id"],
            "destination_account_id": b["id"],
            "amount": 1000,
        },
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    resp = client.get("/events?event_type=transfer.posted", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["type"] == "transfer.posted"


def test_get_unknown_event_returns_404(client, auth_headers):
    resp = client.get("/events/evt_does_not_exist", headers=auth_headers)
    assert resp.status_code == 404


# ---------- webhook endpoint CRUD ----------

def test_create_webhook_endpoint_returns_secret(client, auth_headers):
    resp = _register_endpoint(client, auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"].startswith("whk_")
    assert body["secret"].startswith("whsec_")
    assert len(body["secret"]) > 30  # high entropy
    assert body["event_types"] == ["deposit.posted", "transfer.posted"]


def test_list_endpoints_hides_secret(client, auth_headers):
    _register_endpoint(client, auth_headers)
    resp = client.get("/webhook_endpoints", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert "secret" not in body["data"][0]


def test_get_endpoint_hides_secret(client, auth_headers):
    create = _register_endpoint(client, auth_headers).json()
    resp = client.get(f"/webhook_endpoints/{create['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert "secret" not in resp.json()


def test_delete_endpoint_cancels_pending_deliveries(
    client, auth_headers, db_session
):
    """Deleting an endpoint should dead-letter its pending deliveries."""
    ep = _register_endpoint(client, auth_headers, ["deposit.posted"]).json()
    a = _create_active_account(client, auth_headers)
    _fund(client, auth_headers, a["id"], 5000)

    # Confirm pending delivery exists
    count = db_session.execute(
        text(f"SELECT COUNT(*) FROM webhook_deliveries WHERE status = 'pending'")
    ).scalar_one()
    assert count == 1

    # Delete endpoint
    resp = client.delete(
        f"/webhook_endpoints/{ep['id']}", headers=auth_headers
    )
    assert resp.status_code == 204

    # Pending delivery should now be dead-lettered
    pending = db_session.execute(
        text("SELECT COUNT(*) FROM webhook_deliveries WHERE status = 'pending'")
    ).scalar_one()
    dead = db_session.execute(
        text("SELECT COUNT(*) FROM webhook_deliveries WHERE status = 'dead_lettered'")
    ).scalar_one()
    assert pending == 0
    assert dead == 1


def test_delete_unknown_endpoint_returns_404(client, auth_headers):
    resp = client.delete(
        "/webhook_endpoints/whk_does_not_exist", headers=auth_headers
    )
    assert resp.status_code == 404
