# 0003. Row-level locking for transfer correctness

## Context

A transfer must check the source account has sufficient balance, then
debit it. Between the check and the debit there is a window. Under
concurrency, two transfers could both read the same balance, both pass the
check, and both debit, driving the balance negative. This is the classic
check-then-act race.

Deposits do not have this problem (they only add money, no balance check),
so they do not need locking.

## Decision

In the transfer handler, lock the source account row with
`SELECT ... FOR UPDATE` at the start of the transaction. Hold the lock
through the balance check and the ledger writes. The lock releases on
commit.

The same pattern applies to withdrawals (which also debit and can fail on
insufficient funds) and to reversals (which debit the original
destination).

## Consequences

Positive:
- Concurrent transfers against the same source are serialized: the second
  waits for the first to commit, then sees the updated balance and
  correctly fails if funds are gone. Verified by a stress test that fires
  20 concurrent $100 transfers from a $1000 account; exactly 10 succeed.
- Row-level, not table-level: transfers from different source accounts run
  in parallel, unaffected.

Negative:
- A hot source account (many concurrent transfers from one account)
  serializes through the lock, capping throughput for that account. This is
  inherent to correctness here; you cannot safely parallelize debits
  against one balance.
- Lock held for the duration of the transaction, so the transaction must
  stay short. The handler does no network I/O while holding the lock.

## Status

Accepted.
