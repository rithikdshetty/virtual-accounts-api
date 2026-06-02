# 0002. Synchronous SQLAlchemy over async

## Context

FastAPI supports both sync and async route handlers. SQLAlchemy 2.0
supports both a sync engine (psycopg) and an async engine (asyncpg). The
async stack is often presented as the default "modern" choice.

## Decision

Use synchronous SQLAlchemy with psycopg 3.

## Consequences

Positive:
- The bottleneck of this system is correctness, not request throughput.
  Sync code is easier to reason about, especially around transactions,
  row locks, and the ordering of ledger writes.
- FastAPI runs sync route handlers in a threadpool, so blocking DB calls
  do not stall the event loop. For a prototype's load this is more than
  adequate.
- Testing is simpler: no event-loop fixtures, no async test plumbing for
  the DB layer.

Negative:
- Higher per-request memory and thread overhead than async at high
  concurrency. Not a concern at current scale.
- One real consequence surfaced: the idempotency dependency needs to read
  the raw request body (`await request.body()`), which a sync dependency
  cannot do. That single dependency is async; the rest is sync. The mix is
  deliberate and documented in code.

Production at scale would likely move the hot paths to async + asyncpg and
benchmark the difference.

## Status

Accepted.
