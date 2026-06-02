# Virtual Accounts API

A working REST API for a virtual accounts platform on a master FBO with a
double-entry ledger. Issues virtual accounts to end customers, tracks
balances as liability positions, and keeps every movement provable through
paired journal entries.

**Live site**: https://virtual-accounts-api.onrender.com
**Interactive console**: https://virtual-accounts-api.onrender.com/console
**Interactive docs (Swagger)**: https://virtual-accounts-api.onrender.com/docs
**Rendered OpenAPI spec**: https://rithikdshetty.github.io/virtual-accounts-api/

> Note: the live API runs on a free tier that sleeps after inactivity. The
> first request may take ~30 seconds to wake the service.

## What this is

A system that issues virtual accounts to end customers and tracks their
balances as liability positions on an internal double-entry ledger. All
customer funds physically sit in one underlying FBO bank account. The
ledger is the source of truth for who owns what slice.

The core correctness property: `sum(customer liabilities) == FBO cash`
for every currency, at all times. It is checkable with a single SQL query
and is asserted after every test, including concurrency stress tests.

Implemented:
- Account lifecycle (create, retrieve, list, update status) with a state
  machine: pending → active ↔ frozen → closed
- Balance and per-account transaction history, computed from the ledger
- Internal transfers, atomic and idempotent, with row-level locking on the
  source account to serialize concurrent debits
- Reversal of posted transfers
- External deposits (ACH, wire, RTP)
- External withdrawals
- Webhook subscriptions with HMAC-signed delivery, exponential-backoff
  retries, dead-lettering, and a pullable `/events` log for replay

## Quickstart

Hit the live API in 30 seconds. (Replace the key if you deployed your own.)

```bash
BASE=https://virtual-accounts-api.onrender.com
KEY=sk_test_local_dev_only_change_for_production

# Health check (no auth)
curl -s $BASE/healthz

# Create an account
ACCT=$(curl -s -X POST $BASE/accounts \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"cus_demo","currency":"USD"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

# Activate it
curl -s -X PATCH $BASE/accounts/$ACCT \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"status":"active"}' > /dev/null

# Deposit $100 (idempotent)
curl -s -X POST $BASE/deposits \
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: demo-deposit-001" \
  -H "Content-Type: application/json" \
  -d "{\"account_id\":\"$ACCT\",\"amount\":10000,\"currency\":\"USD\",\"rail\":\"wire\"}"

# Check the balance
curl -s $BASE/accounts/$ACCT/balance -H "Authorization: Bearer $KEY"
```

Or just open the interactive docs and click "Authorize" with the key:
https://virtual-accounts-api.onrender.com/docs

## Web app

The repo ships a static frontend that FastAPI serves itself, so the site and
the API share **one origin** (no CORS) and **one deploy**. Static files live
in `web/` and are mounted last in `app/main.py` — API routers always take
precedence over the static mount.

- **Landing page** (`/`, `web/index.html`) — the marketing/overview page:
  hero, feature tour, the ledger-flow explainer, quickstart, the account and
  external-rail state machines, and an endpoint reference.
- **Interactive console** (`/console`, `web/console.html` + `assets/js/console.js`)
  — a declarative client where each of the 18 endpoints is described as data
  and rendered into a form. It does bearer auth, auto-generates
  `Idempotency-Key`s (with a regenerate button to test idempotent replay),
  formats money to/from minor units, renders RFC 7807 errors, and surfaces
  copyable resource-ID chips so you can chain calls.

Both run entirely against the configured **Base URL** (defaults to the page's
own origin) — set your API key and Base URL once in the top bar and they
persist in `localStorage`.

A few robustness behaviors worth knowing:

- **Base URL self-heal.** A stale saved Base URL that can't work from the
  current page — an `http://` URL on an HTTPS page (mixed content), or a
  `localhost` URL on a remote deploy — is discarded automatically and falls
  back to the page origin.
- **Ad blockers can block the connection probe.** The console's green/red
  "Test" dot pings `/healthz`. Some privacy filter lists (e.g. EasyPrivacy,
  via a `||onrender.com/health` rule) treat any `…onrender.com/health*` path
  as a tracking beacon and block it, so the dot can show **red even when the
  service is healthy** (`net::ERR_BLOCKED_BY_CLIENT`). This only affects the
  health probe — the real API endpoints don't start with `/health`, so all
  actual requests still work. If the dot is red: allowlist the page in your
  blocker (uBlock Origin → power button), or use an incognito window
  (extensions are disabled there). The console's error messages call this out.

## Architecture

- **FastAPI** for the framework; it generates the OpenAPI spec from the
  code, keeping contract and implementation in sync.
- **SQLAlchemy 2.0 (sync)** + **Postgres** (Neon) + **Alembic** migrations.
- **Single double-entry ledger table** as the source of truth. Balances
  are computed, not stored.
- **Background worker thread** for webhook delivery, signing, and retries.

Key decisions are documented as ADRs in `docs/adr/`. The short version is
below.

## Design choices

The decisions worth defending.

**Money as integer minor units.** Floats lose precision under arithmetic.
Every serious payments system stores money as integer counts of minor
units paired with an ISO 4217 code. Not the place to be original.

**Double-entry ledger.** A single balance column is simple and dangerous:
concurrent writes race, reconciliation is hard, provenance is unprovable.
Double-entry forces every movement to produce paired journal entries. The
invariant `sum(customer liabilities) == FBO cash` is checkable
continuously, which is what makes a ledger trustworthy.

**Row-level locking on transfers.** `SELECT ... FOR UPDATE` on the source
account serializes concurrent debits, so the balance check and the debit
are atomic. A concurrency test fires 20 simultaneous transfers from an
account with funds for exactly 10; exactly 10 succeed, 10 fail with
`insufficient_funds`, and the invariant holds.

**Idempotency-Key on every POST.** Network retries are a fact of life and
double-charging is not survivable. Server stores key + request-body hash +
response for 24 hours. Same key + same body replays the cached response;
same key + different body returns 409.

**Cursor pagination.** Offset pagination is O(n) and unstable under
inserts. Opaque cursors are O(log n) and stable, and hide the underlying
strategy from clients.

**RFC 7807 problem+json errors with a stable `code` field.** Clients
branch on `code` (`insufficient_funds`), never on prose.

**Resource-prefixed IDs** (`acct_`, `tfr_`, `dep_`, `wdl_`). Readable logs;
catches the class of bug where the wrong ID lands at the wrong endpoint.

**Webhook signing: timestamp + HMAC-SHA256**, Stripe-format
`t=<ts>,v1=<hmac>`. The timestamp is part of the signed string, preventing
replay. Receivers reject deliveries older than 5 minutes.

**`/events` endpoint for replay.** Push-only delivery loses events when the
consumer is down past the retry budget. A pullable event log is how robust
integrations are built. Stripe, Modern Treasury, Adyen all have one.

**Reversals as a dedicated endpoint.** `POST /transfers/{id}/reversal`
makes "must reference an existing transfer" a structural constraint, not a
runtime flag check.

## Testing

95+ tests covering auth, validation, the account state machine, the ledger
invariant, idempotency replay and conflict, all transfer/withdrawal failure
modes, webhook signing and retry/dead-letter behavior, and two concurrency
stress tests (concurrent deposits and concurrent transfers against the same
account, both asserting the invariant holds afterward).

```bash
pytest tests/ -v
```

## Known limitations (prototype scope)

Deliberately out of scope for v0.1; these are what would change for
production:

- **Reconciliation against bank files** (BAI2, NACHA returns) is not
  implemented. A real ledger reconciles against the bank's record of the
  FBO account daily; this prototype trusts its own writes.
- **Idempotency is not atomic under concurrent identical requests.** Two
  simultaneous POSTs with the same key could both execute. Production would
  add a Postgres advisory lock keyed on the idempotency key.
- **The webhook worker is an in-process thread.** Scaling past one app
  instance needs a distributed queue or `FOR UPDATE SKIP LOCKED`
  coordination. On the free hosting tier the worker also sleeps when the
  app sleeps.
- **Webhook secrets are stored as plaintext** in a column. Production would
  encrypt at rest with a KMS-managed key.
- **No KYC, fraud screening, FX, statements, card issuing, or backdating.**
- **External rails are simulated.** Deposits and withdrawals post
  synchronously; a real integration would carry a pending → posted
  transition driven by bank confirmations.

See `NOTES.md` for the longer list and the open questions I'd need a real
bank partner to answer.

## License

Proprietary. Shared for review.
