"""
Webhook delivery worker.

Architecture:
- A single daemon thread polls the webhook_deliveries table every
  POLL_INTERVAL seconds.
- For each pending delivery whose next_attempt_at has passed, it loads
  the event payload and the endpoint's secret, signs the payload with
  HMAC-SHA256 + timestamp, and POSTs to the customer URL.
- On 2xx response: status -> succeeded
- On non-2xx, network error, or timeout: schedule retry with
  exponential backoff. After MAX_AGE_HOURS of failures: dead-letter.

Production trade-offs documented as known limitations:

1. **Single-process worker**: this thread runs inside the FastAPI app.
   Scaling beyond one app instance requires distributed coordination
   (e.g. row-level lock with FOR UPDATE SKIP LOCKED, or a separate
   queue like Redis/SQS/Temporal). Multiple worker threads in the same
   process would race on rows.

2. **At-least-once delivery**: if the server crashes between sending
   the HTTP request and updating the delivery row, the customer may
   receive the same event twice. Customer-side idempotency by event.id
   handles this correctly.

3. **No backpressure**: if 1M events fire at once, the worker still
   polls 10 at a time. Production would tune the batch size and add
   per-endpoint rate limiting.

4. **Signature format**: `t=<unix_timestamp>,v1=<hex_hmac>` matches
   Stripe's convention. v1 versioning lets us rotate algorithms in
   future.
"""
import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Event, WebhookDelivery, WebhookEndpoint


logger = logging.getLogger(__name__)


# Tunables
POLL_INTERVAL = 5  # seconds between polls
BATCH_SIZE = 10
MAX_AGE_HOURS = 24
HTTP_TIMEOUT = 10  # seconds per delivery attempt
BACKOFF_SCHEDULE = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 3600]
# After exhausting the schedule, retries cap at 3600s (1 hour) until
# the 24-hour budget runs out.


def _next_backoff(attempt_count: int) -> int:
    """Return seconds to wait before the next attempt."""
    if attempt_count < len(BACKOFF_SCHEDULE):
        return BACKOFF_SCHEDULE[attempt_count]
    return 3600


def _sign_payload(secret: str, payload: str, timestamp: int) -> str:
    """
    Compute the HMAC-SHA256 signature in the format Stripe uses:
      Webhook-Signature: t=<timestamp>,v1=<hex_hmac>

    The signed string is `<timestamp>.<payload>`, preventing replays
    that swap the timestamp.
    """
    signed_string = f"{timestamp}.{payload}".encode("utf-8")
    signature = hmac.new(
        secret.encode("utf-8"), signed_string, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def _attempt_delivery(
    db: Session, delivery: WebhookDelivery
) -> None:
    """
    Send one delivery. Updates the delivery row in place.
    Caller commits.
    """
    endpoint = db.get(WebhookEndpoint, delivery.endpoint_id)
    if not endpoint or endpoint.status != "active":
        delivery.status = "dead_lettered"
        delivery.last_attempt_at = datetime.now(timezone.utc)
        return

    event = db.get(Event, delivery.event_id)
    if not event:
        delivery.status = "dead_lettered"
        delivery.last_attempt_at = datetime.now(timezone.utc)
        return

    payload = {
        "id": event.id,
        "object": "event",
        "type": event.event_type,
        "livemode": event.livemode,
        "created_at": event.created_at.isoformat(),
        "data": event.data,
    }
    payload_json = json.dumps(payload, default=str, separators=(",", ":"))
    timestamp = int(time.time())
    signature = _sign_payload(endpoint.secret, payload_json, timestamp)

    headers = {
        "Content-Type": "application/json",
        "Webhook-Signature": signature,
        "User-Agent": "VirtualAccountsWebhooks/0.1",
    }

    now = datetime.now(timezone.utc)
    delivery.last_attempt_at = now
    delivery.attempt_count += 1

    response_status = None
    response_snippet = None

    try:
        resp = httpx.post(
            endpoint.url,
            content=payload_json,
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
        response_status = resp.status_code
        response_snippet = resp.text[:500] if resp.text else None

        if 200 <= resp.status_code < 300:
            delivery.status = "succeeded"
            delivery.next_attempt_at = None
            logger.info(f"Delivered {delivery.id} to {endpoint.url} ({resp.status_code})")
        else:
            _schedule_retry_or_dead_letter(delivery)
            logger.warning(
                f"Delivery {delivery.id} failed: {resp.status_code}"
            )

    except (httpx.RequestError, httpx.TimeoutException) as e:
        response_snippet = f"network error: {type(e).__name__}: {str(e)[:400]}"
        _schedule_retry_or_dead_letter(delivery)
        logger.warning(f"Delivery {delivery.id} network error: {e}")

    delivery.response_status = response_status
    delivery.response_body_snippet = response_snippet


def _schedule_retry_or_dead_letter(delivery: WebhookDelivery) -> None:
    """Decide whether to retry or give up."""
    age = datetime.now(timezone.utc) - delivery.created_at
    if age > timedelta(hours=MAX_AGE_HOURS):
        delivery.status = "dead_lettered"
        delivery.next_attempt_at = None
    else:
        backoff = _next_backoff(delivery.attempt_count)
        delivery.next_attempt_at = datetime.now(timezone.utc) + timedelta(
            seconds=backoff
        )
        delivery.status = "pending"


def _process_batch() -> int:
    """
    Find pending deliveries due for attempt; process them.
    Returns number processed.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        # Pick up pending deliveries due for attempt
        stmt = (
            select(WebhookDelivery)
            .where(WebhookDelivery.status == "pending")
            .where(WebhookDelivery.next_attempt_at <= now)
            .order_by(WebhookDelivery.created_at)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)  # so multiple workers (if any) don't double-process
        )
        deliveries = list(db.execute(stmt).scalars())

        for delivery in deliveries:
            _attempt_delivery(db, delivery)

        db.commit()
        return len(deliveries)
    except Exception as e:
        logger.exception(f"Worker batch failed: {e}")
        db.rollback()
        return 0
    finally:
        db.close()


_worker_stop = threading.Event()
_worker_thread: threading.Thread | None = None


def _worker_loop() -> None:
    """The main loop. Polls until told to stop."""
    logger.info("Webhook worker started")
    while not _worker_stop.is_set():
        try:
            processed = _process_batch()
            if processed == 0:
                # Nothing to do; sleep the full poll interval
                _worker_stop.wait(POLL_INTERVAL)
            else:
                # Pull next batch quickly if we just processed work
                _worker_stop.wait(0.1)
        except Exception as e:
            logger.exception(f"Worker loop error: {e}")
            _worker_stop.wait(POLL_INTERVAL)
    logger.info("Webhook worker stopped")


def start_worker() -> None:
    """Start the background worker thread. Idempotent."""
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _worker_stop.clear()
    _worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="webhook-worker")
    _worker_thread.start()


def stop_worker(timeout: float = 5.0) -> None:
    """Stop the worker. Used in tests and shutdown."""
    global _worker_thread
    _worker_stop.set()
    if _worker_thread is not None:
        _worker_thread.join(timeout=timeout)
        _worker_thread = None


def process_once() -> int:
    """
    Process one batch synchronously. Useful for tests where you want
    deterministic timing instead of waiting for the polling loop.
    """
    return _process_batch()
