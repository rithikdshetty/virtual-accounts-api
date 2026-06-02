# 0006. Prefixed ULIDs over UUIDv4

## Context

Every resource needs a unique ID. The common default is UUIDv4 (random).
We also want IDs that are pleasant to work with in logs and that catch a
class of bug.

## Decision

Use ULIDs with a resource-type prefix: `acct_01J...`, `tfr_01J...`,
`dep_01J...`, `wdl_01J...`, `le_01J...`, `evt_01J...`, `whk_01J...`,
`whd_01J...`.

## Consequences

Positive:
- ULIDs are time-sortable (lexicographically increasing), which gives
  better B-tree index locality than random UUIDv4 when the ID is the
  primary key.
- The type prefix makes logs and stack traces readable at a glance and
  catches the bug where the wrong ID type is passed to the wrong endpoint
  (a `tfr_` arriving where an `acct_` is expected is obvious).
- Still globally unique and non-sequential enough to not leak volume the
  way an auto-increment integer would.

Negative:
- Slightly longer than a bare integer. Irrelevant at this scale.
- The timestamp embedded in a ULID leaks creation time. Acceptable here;
  for IDs exposed to untrusted parties where that matters, a random scheme
  would be used instead.

## Status

Accepted.
