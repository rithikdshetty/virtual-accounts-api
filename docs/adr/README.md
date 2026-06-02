# Architecture Decision Records

Short records of the design decisions in this project, why they were made,
and what was traded off. Format: Context / Decision / Consequences.

These exist so the reasoning survives past the moment of decision, and so
a reviewer can see which choices were deliberate.

| ADR | Decision |
|-----|----------|
| [0001](0001-double-entry-ledger.md) | Double-entry ledger as the source of truth |
| [0002](0002-sync-sqlalchemy.md) | Synchronous SQLAlchemy over async |
| [0003](0003-row-locking-for-transfers.md) | Row-level locking for transfer correctness |
| [0004](0004-idempotency-strategy.md) | Per-endpoint idempotency keyed on request hash |
| [0005](0005-webhook-delivery.md) | In-process worker for webhook delivery |
| [0006](0006-ulid-ids.md) | Prefixed ULIDs over UUIDv4 |
| [0007](0007-no-foreign-keys.md) | No foreign-key constraints on the ledger |
