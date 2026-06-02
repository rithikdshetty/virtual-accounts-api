# 0007. No foreign-key constraints on the ledger

## Context

`ledger_entries.account_id`, `transfers.source_account_id`, and similar
columns reference accounts, but the schema declares no foreign-key
constraints. This is a deliberate and debatable choice.

## Decision

Omit foreign-key constraints. Rely on application-layer transactions for
referential integrity: every write that creates related rows does so inside
a single transaction that validates the references first.

## Consequences

Positive:
- Lower write overhead on the ledger's hot path. FK constraint checks run
  on every insert/update/delete; on a high-write ledger that cost adds up.
- Production payments ledgers commonly denormalize and enforce integrity in
  the application for exactly this reason.

Negative:
- The database will not stop a buggy or manual write from inserting a
  ledger entry referencing a nonexistent account. The safety net is
  application logic plus tests, not the schema.
- This is the choice a reviewer is most likely to push back on. The honest
  answer: it optimizes the ledger write path, and if integrity bugs
  appeared in practice I would add the constraints and benchmark the impact
  rather than assume the overhead is prohibitive.

## Status

Accepted, acknowledged as the most debatable decision here.
