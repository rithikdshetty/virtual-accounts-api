"""
Tests for the webhook delivery worker.

The worker imports SessionLocal directly (not via FastAPI's dependency
injection), so we monkey-patch it to point at the test database for the
duration of each test. Without this, process_once() queries the wrong DB.

We use process_once() to run one synchronous batch deterministically
instead of waiting for the polling loop. httpx.post is mocked so tests
don't actually hit the network.
"""
import hashlib
import hmac
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import text

import app.lib.webhook_worker as worker_module
from app.lib.webhook_worker import _sign_payload, process_once
from app.models import WebhookDelivery


# ---------- session override fixture ----------

@pytest.fixture(autouse=True)
def patch_worker_session(db_session):
    """
    Replace the worker's SessionLocal with one bound to the test engine
    for every test in this file. The db_session fixture ensures the
    test DB is reset before each test.
    """
    from tests.conftest import TestSessionLocal
    original = worker_module.SessionLocal
    worker_module.SessionLocal = TestSessionLocal
    yield
    worker_module.SessionLocal = original


# ---------- helpers ----------

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


def _fund_and_get_delivery(client, auth_headers, db_session, account_id):
    client.post(
        "/deposits",
        json={
            "account_id": account_id,
            "amount": 5000,
            "currency": "USD",
            "rail": "wire",
        },
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    db_session.expire_all()
    delivery = (
        db_session.query(WebhookDelivery)
        .filter(WebhookDelivery.status == "pending")
        .first()
    )
    assert delivery is not None, "expected a pending delivery"
    return delivery


@pytest.fixture
def endpoint_with_secret(client, auth_headers):
    resp = client.post(
        "/webhook_endpoints",
        json={
            "url": "http://localhost:9000/webhook",
            "event_types": ["deposit.posted", "transfer.posted"],
        },
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    body = resp.json()
    return body, body["secret"]


# ---------- signature ----------

def test_sign_payload_produces_verifiable_hmac():
    secret = "whsec_test_secret"
    payload = '{"id":"evt_test","type":"deposit.posted"}'
    timestamp = 1717000000

    signature_header = _sign_payload(secret, payload, timestamp)
    parts = dict(p.split("=", 1) for p in signature_header.split(","))
    assert int(parts["t"]) == timestamp
    assert len(parts["v1"]) == 64

    expected = hmac.new(
        secret.encode(),
        f"{timestamp}.{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert parts["v1"] == expected


# ---------- successful delivery ----------

def test_successful_delivery_marks_succeeded(
    client, auth_headers, db_session, endpoint_with_secret
):
    a = _create_active_account(client, auth_headers)
    delivery = _fund_and_get_delivery(client, auth_headers, db_session, a["id"])

    received = []

    def post_shim(url, content=None, headers=None, timeout=None):
        received.append({
            "url": url,
            "signature": headers.get("Webhook-Signature"),
            "body": content,
        })
        return httpx.Response(200, text="ok")

    with patch("app.lib.webhook_worker.httpx.post", side_effect=post_shim):
        processed = process_once()

    assert processed >= 1
    assert len(received) == 1

    db_session.expire_all()
    refreshed = db_session.get(WebhookDelivery, delivery.id)
    assert refreshed.status == "succeeded"
    assert refreshed.response_status == 200
    assert refreshed.attempt_count == 1
    assert refreshed.next_attempt_at is None


def test_signature_in_request_is_verifiable_by_receiver(
    client, auth_headers, db_session, endpoint_with_secret
):
    _, secret = endpoint_with_secret
    a = _create_active_account(client, auth_headers)
    _fund_and_get_delivery(client, auth_headers, db_session, a["id"])

    captured = {}

    def post_shim(url, content=None, headers=None, timeout=None):
        captured["signature"] = headers.get("Webhook-Signature")
        captured["body"] = content
        return httpx.Response(200, text="ok")

    with patch("app.lib.webhook_worker.httpx.post", side_effect=post_shim):
        process_once()

    assert "signature" in captured, "delivery never made the HTTP call"

    parts = dict(p.split("=", 1) for p in captured["signature"].split(","))
    timestamp = int(parts["t"])
    sig = parts["v1"]

    assert abs(int(time.time()) - timestamp) < 30

    body = captured["body"] if isinstance(captured["body"], str) else captured["body"].decode()
    expected = hmac.new(
        secret.encode(),
        f"{timestamp}.{body}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert hmac.compare_digest(sig, expected)


# ---------- failure and retry ----------

def test_non_2xx_response_schedules_retry(
    client, auth_headers, db_session, endpoint_with_secret
):
    a = _create_active_account(client, auth_headers)
    delivery = _fund_and_get_delivery(client, auth_headers, db_session, a["id"])

    def post_shim(url, content=None, headers=None, timeout=None):
        return httpx.Response(500, text="internal server error")

    with patch("app.lib.webhook_worker.httpx.post", side_effect=post_shim):
        processed = process_once()
    assert processed == 1

    db_session.expire_all()
    refreshed = db_session.get(WebhookDelivery, delivery.id)
    assert refreshed.status == "pending"
    assert refreshed.attempt_count == 1
    assert refreshed.response_status == 500
    assert refreshed.next_attempt_at is not None
    assert refreshed.next_attempt_at > datetime.now(timezone.utc)


def test_network_error_schedules_retry(
    client, auth_headers, db_session, endpoint_with_secret
):
    a = _create_active_account(client, auth_headers)
    delivery = _fund_and_get_delivery(client, auth_headers, db_session, a["id"])

    def post_shim(url, content=None, headers=None, timeout=None):
        raise httpx.ConnectError("connection refused")

    with patch("app.lib.webhook_worker.httpx.post", side_effect=post_shim):
        processed = process_once()
    assert processed == 1

    db_session.expire_all()
    refreshed = db_session.get(WebhookDelivery, delivery.id)
    assert refreshed.status == "pending"
    assert refreshed.attempt_count == 1
    assert refreshed.next_attempt_at is not None
    snippet = (refreshed.response_body_snippet or "").lower()
    assert "network" in snippet or "connect" in snippet


def test_retry_backoff_grows(
    client, auth_headers, db_session, endpoint_with_secret
):
    a = _create_active_account(client, auth_headers)
    delivery = _fund_and_get_delivery(client, auth_headers, db_session, a["id"])

    def post_shim(url, content=None, headers=None, timeout=None):
        return httpx.Response(500)

    with patch("app.lib.webhook_worker.httpx.post", side_effect=post_shim):
        process_once()

    db_session.execute(
        text(
            "UPDATE webhook_deliveries SET next_attempt_at = NOW() - INTERVAL '1 second' WHERE id = :id"
        ),
        {"id": delivery.id},
    )
    db_session.commit()

    with patch("app.lib.webhook_worker.httpx.post", side_effect=post_shim):
        process_once()

    db_session.expire_all()
    refreshed = db_session.get(WebhookDelivery, delivery.id)
    assert refreshed.attempt_count == 2


# ---------- dead-letter ----------

def test_dead_letter_after_24h(
    client, auth_headers, db_session, endpoint_with_secret
):
    a = _create_active_account(client, auth_headers)
    delivery = _fund_and_get_delivery(client, auth_headers, db_session, a["id"])

    db_session.execute(
        text(
            "UPDATE webhook_deliveries SET created_at = NOW() - INTERVAL '25 hours', "
            "next_attempt_at = NOW() - INTERVAL '1 minute' WHERE id = :id"
        ),
        {"id": delivery.id},
    )
    db_session.commit()

    def post_shim(url, content=None, headers=None, timeout=None):
        return httpx.Response(500)

    with patch("app.lib.webhook_worker.httpx.post", side_effect=post_shim):
        process_once()

    db_session.expire_all()
    refreshed = db_session.get(WebhookDelivery, delivery.id)
    assert refreshed.status == "dead_lettered"
    assert refreshed.next_attempt_at is None


# ---------- worker scheduling ----------

def test_worker_skips_deliveries_not_yet_due(
    client, auth_headers, db_session, endpoint_with_secret
):
    a = _create_active_account(client, auth_headers)
    delivery = _fund_and_get_delivery(client, auth_headers, db_session, a["id"])

    db_session.execute(
        text(
            "UPDATE webhook_deliveries SET next_attempt_at = NOW() + INTERVAL '10 minutes' WHERE id = :id"
        ),
        {"id": delivery.id},
    )
    db_session.commit()

    call_count = [0]

    def post_shim(*args, **kwargs):
        call_count[0] += 1
        return httpx.Response(200)

    with patch("app.lib.webhook_worker.httpx.post", side_effect=post_shim):
        processed = process_once()

    assert processed == 0
    assert call_count[0] == 0

    db_session.expire_all()
    refreshed = db_session.get(WebhookDelivery, delivery.id)
    assert refreshed.attempt_count == 0
