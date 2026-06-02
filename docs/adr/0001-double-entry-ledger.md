# 0001. Double-entry ledger as the source of truth

## Context

The system tracks balances for many virtual accounts whose funds all sit
in one underlying FBO bank account. We need a representation of "who owns
what slice" that is correct under concurrency, auditable, and provable.

The naive option is a single `balance` column on the accounts table,
updated on every movement.

## Decision

Use a double-entry ledger. Every monetary movement produces one or more
rows in a `ledger_entries` table. Balances are computed as the sum of an
account's entries, never stored.

- Deposits: two positive entries (FBO cash up, customer liability up)
- Transfers: one negative and one positive entry that net to zero; FBO
  cash unchanged
- Withdrawals: two negative entries (FBO cash down, customer down)

The defining invariant: for every currency,
`sum(entries on customer accounts) == sum(entries on the FBO cash account)`.

## Consequences

Positive:
- The invariant is checkable with one SQL query, continuously. This is the
  property that makes a ledger trustworthy and is what failed at firms like
  Synapse.
- Full provenance: every balance is explained by its entries.
- Reversals and corrections are new entries, never mutations, so history
  is intact for audit.

Negative:
- Reads are more expensive: a balance is a SUM, not a column read. Mitigated
  for now by indexing `(account_id, posted_at)`. At scale, a materialized
  balance refreshed asynchronously would be added, with the ledger remaining
  the source of truth.
- More rows written per operation (2+ per movement).

## Status

Accepted.
