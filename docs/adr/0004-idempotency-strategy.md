# 0004. Per-endpoint idempotency keyed on request hash

## Context

Clients retry failed requests. Without protection, a retried POST could
create a second deposit, transfer, or withdrawal, double-moving money.
This is not survivable in a payments system.

## Decision

Every POST requires an `Idempotency-Key` header. The server:
1. Hashes the canonical (sorted-key, whitespace-stripped) request body.
2. Looks up `(api_key_hash, idempotency_key)` in an `idempotency_keys`
   table.
3. If found with the same request hash: returns the cached response, does
   not re-execute.
4. If found with a different request hash: returns 409 (key reused with a
   different body).
5. If not found: executes, caches the response for 24 hours, returns it.

The key is scoped per API key (composite primary key) so two tenants using
the same UUID do not collide. Hashing the body, rather than trusting the
key alone, catches clients that reuse a key with changed content.

## Consequences

Positive:
- Safe retries. The most important property for a money-moving API.
- Canonical JSON hashing makes replays insensitive to key ordering.

Negative:
- Not atomic under concurrent identical requests. Two simultaneous POSTs
  with the same key can both miss the cache and execute. The correct fix is
  a Postgres advisory lock keyed on `hash(api_key + idempotency_key)` at the
  start of the request, serializing same-key requests while letting
  unrelated requests proceed. Deferred for v0.1; documented as a known
  limitation.
- Stores the full response body. Wasteful for large responses; v1.0 would
  store a compact form.

## Status

Accepted, with the concurrency gap documented.
