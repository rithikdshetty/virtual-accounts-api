# 0005. In-process worker for webhook delivery

## Context

When a business event posts (deposit, transfer, withdrawal), subscribed
customers need an HTTP notification. Delivery must survive transient
receiver failures (retries), be verifiable (signing), and not lose events
permanently (replay).

## Decision

- Emit an `Event` row and pending `WebhookDelivery` rows in the same
  transaction as the business event (a simplified transactional outbox).
- A background daemon thread polls `webhook_deliveries` every 5 seconds for
  due deliveries, signs each payload with HMAC-SHA256 in Stripe format
  (`t=<ts>,v1=<hmac>`), and POSTs to the customer URL.
- On 2xx: mark succeeded. On failure: schedule a retry with exponential
  backoff (1s, 2s, 4s ... capped at 1h). After a 24h budget: dead-letter.
- A pullable `GET /events` endpoint lets customers backfill missed events.

## Consequences

Positive:
- Emission is atomic with the business event: no events for rolled-back
  transactions, no missing events for committed ones.
- Signing with a timestamped HMAC prevents forgery and replay.
- The `/events` replay endpoint means a downtime window is recoverable, not
  a permanent loss.

Negative:
- The worker is a thread inside the web process. Scaling past one instance
  requires distributed coordination (`FOR UPDATE SKIP LOCKED`, already used
  defensively, or a real queue like SQS/Temporal). On free hosting the
  worker also sleeps when the app sleeps.
- At-least-once delivery: a crash between sending and recording can
  redeliver. Receivers must dedupe on `event.id`.
- Production would run delivery as a separate always-on process, not a
  thread.

## Status

Accepted, with scaling path documented.
