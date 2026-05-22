"""
Resource ID generation. Every resource ID is prefixed with its type
(acct_, tfr_, dep_, etc.) so that logs and stack traces are readable
at a glance, and so the class of bug where the wrong ID type is passed
to the wrong endpoint is caught immediately.

We use ULIDs rather than UUIDv4 because:
- They're time-sortable (lexicographically increasing), so DB indexes
  on ID are more efficient (better B-tree locality).
- They're shorter than UUIDs as base32 strings.
- They contain a timestamp, which is occasionally useful for debugging.
"""
from ulid import ULID


def new_id(prefix: str) -> str:
    """
    Generate a new prefixed ULID. Example: new_id('acct') -> 'acct_01J...'
    """
    return f"{prefix}_{ULID()}"
