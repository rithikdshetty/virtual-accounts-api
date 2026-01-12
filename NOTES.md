# Notes

Working file. Open questions, things I'm not sure about, choices I might
revisit. Not polished, on purpose.

## Things I'm chewing on

**Hold model.** Right now I have posted, failed, reversed for transfers,
and pending, posted, failed for external deposits and withdrawals. Real
card-issuing systems also have authorization holds that are distinct from
posted. I don't have card issuing in v0.1, but if I add ACH same-day
returns properly, I probably need a `held` or `provisional` state on
deposits. Punting until I see a real reconciliation file from a bank
partner.

**Backdating.** Operators sometimes need to post correction entries with
an as-of date in the past (e.g., bank confirms a wire received Friday but
we record it Monday). My current schema uses a single `posted_at`. If I
need backdating, I'd add `effective_at` and keep `posted_at` as
system-time. Defer.

**Webhook event payload typing.** The `Event.data` field is `type: object`
without further constraints. SDK code generators love discriminated unions
(oneOf with a discriminator on `type`). I avoided it because OpenAPI
discriminator tooling is uneven across generators and I'd rather have a
correct-but-loose spec than a tight one that breaks half the codegen
tools. Marked the events endpoint experimental partly because of this.

**`X-Idempotent-Replay: true` response header.** Stripe sets this when a
request was a replay vs newly executed. I echo `idempotency_key` in the
response body but a header is more idiomatic for replay detection. Will
add when I do FastAPI.

**Per-API-key rate limits.** Headers (`X-RateLimit-Limit`, `Remaining`,
`Reset`) are standard but I haven't designed the tier model. Single rate
per key for v0.1.

**Multi-tenant / platform model.** Right now I assume one operator, one
set of customers. If this ever becomes a Banking-as-a-Service product
with sub-tenants, I'd need an Application/Connected Account resource
above this whole API. That's a v1.0 conversation.

## Decisions I might be wrong about

**Same-currency lock on accounts.** I could have made the account
multi-currency with per-currency balances inside it. I chose
one-currency-per-account because it matches how every bank partner I've
read about actually issues virtual accounts. If I'm wrong, the migration
is painful (split each multi-currency account into N).

**Internal transfers post synchronously.** This is correct for a pure
ledger move. But if I ever add fraud screening on internal transfers, the
synchronous model breaks. Then I'd need a pending state. Possible v0.2.

**Cursor pagination using opaque cursor instead of last-id.** Opaque
cursors let me change the underlying pagination strategy without breaking
clients. Last-id is simpler to debug. I went with opaque. Mild
preference, could flip.

**Reversals as a dedicated endpoint vs a flag on POST /transfers.** I
moved to a dedicated `POST /transfers/{id}/reversal` because it makes the
constraint "must reference an existing transfer" structural rather than
runtime. Trade-off: now there are two ways to create a transfer-like
resource, which is mildly ugly.

## Things I deliberately didn't include

- KYC tiers
- Statements / PDF generation
- FX
- Card issuing
- Negative balances / overdrafts
- Backdating
- Bulk operations (CSV upload of transfers)
- Mobile SDK / OAuth scopes
- Webhook IP allowlist on egress

Each of these is real product surface but each is also a multi-week
project on its own. v0.1 is "can you move money safely between virtual
accounts and observe what happened." Everything else is layered on top.

## Open questions for the bank partner

(For when there is one. None of these are answerable solo.)

1. Do they support same-day ACH origination, or next-day only?
2. What's the cutoff time for wire originations?
3. Reconciliation file format (BAI2, BAI1, NACHA, proprietary)?
4. How are ACH returns surfaced? Direct webhook from them, or in the
   daily file?
5. FedNow membership? RTP membership?
6. FBO account: single FBO across all customers, or pooled-per-product?
7. Their SLO for confirming a wire receipt?
8. Per-account, per-customer transaction limits at the bank level?
