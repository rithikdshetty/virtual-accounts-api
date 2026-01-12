# Virtual Accounts API

OpenAPI 3.1 spec for a virtual accounts platform on a master FBO with a
double-entry ledger.

**Rendered spec**: https://YOUR_USERNAME.github.io/virtual-accounts-api/

**Status**: design + spec only. FastAPI implementation is the next step;
see roadmap below. Currently on version 0.1.2 after a self-review pass
(see CHANGELOG.md).

## What this is

A REST contract for a system that issues virtual accounts to end
customers and tracks their balances as liability positions on an internal
double-entry ledger. All customer funds physically sit in one underlying
FBO bank account. The ledger is the source of truth for who owns what
slice.

Covered:
- Account lifecycle (create, retrieve, list, update status, close)
- Balance and per-account transaction history
- Internal transfers, atomic and idempotent
- Reversal of posted transfers
- External deposits (ACH, wire, RTP)
- External withdrawals
- Webhook subscriptions, signed delivery, and a pullable `/events` log
  for replay

Not covered in v0.1 (see NOTES.md for the longer list):
- KYC tiers
- Statements
- FX
- Card issuing
- Backdating
- Bulk operations

## Design choices

The decisions worth defending. I went back and forth on some of these
and ended up here.

**Money as integer minor units.** Floats lose precision under arithmetic.
Stripe, Modern Treasury, every serious payments system stores money as
integer counts of minor units paired with an ISO 4217 code. This is not
the place to be original.

**Double-entry ledger.** A single balance column on the accounts table
is simple and dangerous: concurrent writes race, reconciliation is hard,
you cannot prove provenance. Double-entry forces every movement to
produce paired journal entries that net to zero. The invariant
`sum(customer liabilities) == FBO cash` is checkable continuously, which
is what makes a ledger trustworthy.

**Idempotency-Key on every POST.** Network retries are a fact of life and
double-charging users is not survivable. Any opaque string up to 255
chars is accepted (I narrowed this to UUID-only in 0.1.0 and walked it
back; UUID-only is needlessly restrictive). Server stores key + request
hash + response for 24 hours.

**Cursor pagination.** Offset pagination is O(n) and unstable under
inserts. Cursors are O(log n) and stable. Opaque cursors instead of
last-id to let me change the underlying strategy without breaking
clients.

**RFC 7807 problem+json errors with a stable `code` field.** Clients
should branch on `code` (`insufficient_funds`), never on prose. `title`
and `detail` are human aids and can change.

**Resource-prefixed IDs.** `acct_`, `tfr_`, `txn_`, `dep_`, `wdr_`. Lets
logs and stack traces be read at a glance. Catches the class of bug
where the wrong ID lands at the wrong endpoint.

**Webhook signing: timestamp + HMAC-SHA256.** Verifier computes
`HMAC_SHA256(secret, timestamp + "." + raw_body)`. Timestamp prevents
replay even if a delivery is captured. Receivers reject deliveries older
than 5 minutes.

**`/events` endpoint for webhook replay.** Push-only delivery loses
events when the consumer is down past the retry budget. A pullable event
log is how you build robust integrations. Stripe, Modern Treasury,
Square, Adyen all have one. Adding this was the biggest fix in 0.1.2.

**Reversals as a dedicated endpoint.** `POST /transfers/{id}/reversal`
makes the constraint "must reference an existing transfer" structural,
not a runtime check on a flag. Trade-off: now there are two ways to
create a transfer-like resource. I think this is worth it.

**Internal transfers post synchronously, external rails are async.** A
transfer between two virtual accounts is one DB transaction. ACH, wire,
RTP settle on bank-side schedules, so deposits and withdrawals carry a
pending then posted transition driven by external confirmations.

## Open questions

See NOTES.md for things I'm still thinking about (hold model,
backdating, multi-tenancy, etc.) and the list of questions I'd need to
answer with a real bank partner.

## Hosting

GitHub Pages serves `/docs/index.html`, which loads `openapi.yaml` via
Redoc from the CDN. No build step. Edit YAML, push, refresh.

## Roadmap

- [x] OpenAPI 3.1 spec
- [x] Self-review pass (0.1.2)
- [ ] FastAPI implementation with Pydantic models matching the spec
- [ ] Postgres schema with double-entry ledger
- [ ] Idempotency middleware
- [ ] Webhook delivery worker (HMAC signing + exponential backoff)
- [ ] Fly.io deployment
- [ ] Loom demo

## License

Proprietary. Spec shared for review.
