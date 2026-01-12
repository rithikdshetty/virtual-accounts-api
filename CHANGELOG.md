# Changelog

## 0.1.2

After self-review. Mostly closing gaps that a careful reviewer would
catch.

Added:
- `GET /events` and `GET /events/{event_id}` for webhook replay. Big
  omission in 0.1.0. Marked experimental until the implementation
  forces a payload-typing decision (see NOTES.md).
- `POST /transfers/{transfer_id}/reversal` as a dedicated endpoint.
  Replaces the `reverses_transfer_id` flag on `POST /transfers`.
- `GET /healthz` liveness probe.
- `Request-Id` response header semantics (was on Error schema only,
  now on every response).
- `livemode` boolean on Account, Transfer, Deposit, Withdrawal, Event.
- `idempotency_key` echoed in response bodies.

Changed:
- `Idempotency-Key` accepts any string up to 255 chars (was UUID-only).
- `TransferCreateRequest.currency` removed. Currency is implied by
  source account.
- `Balance.available` description rewritten. The previous wording
  referenced "pending outbound holds" without defining where they come
  from. Now explicit: holds come only from pending withdrawals.
- Top-level description expanded with explicit UTC requirement,
  currency lock, closed account semantics, transaction limits per rail,
  and webhook clock skew tolerance.

Removed:
- `Money` schema (was defined but never used). Documented the
  alternative in a comment in the spec.

Known limitations (carried forward):
- No richer ledger states (hold, authorized, settled). See NOTES.md.
- No backdating. See NOTES.md.
- No FX. See NOTES.md.
- No tiered limits / discoverable limits endpoint.

## 0.1.0

Initial spec. Covers virtual account lifecycle, internal transfers,
external deposits and withdrawals, and webhook subscription management.
