"""seed FBO cash account for double-entry ledger

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-22

The FBO cash account represents the actual master bank account holding
all customer funds. It is one row per supported currency.

Every deposit produces two ledger entries:
  - debit FBO cash:        amount=+10000 on fbo_cash_USD
  - credit customer:       amount=+10000 on acct_xxx

Every withdrawal produces:
  - credit FBO cash:       amount=-10000 on fbo_cash_USD
  - debit customer:        amount=-10000 on acct_xxx

Invariant: SUM(amount WHERE account_id = 'fbo_cash_USD') equals
SUM(amount WHERE account_id LIKE 'acct_%' AND currency = 'USD')

This row uses a special id prefix `fbo_` and customer_id `internal` so
it's never returned by customer-facing list queries. Routers filter it
out explicitly.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Seed the USD FBO cash account. New currencies need their own seed
    # rows; we'll add them in future migrations as needed.
    op.execute(
        """
        INSERT INTO accounts (
            id, customer_id, currency, status, livemode, metadata_json,
            created_at, updated_at
        )
        VALUES (
            'fbo_cash_USD',
            'internal',
            'USD',
            'active',
            false,
            '{"description": "Master FBO cash account for USD"}',
            NOW(),
            NOW()
        )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM accounts WHERE id = 'fbo_cash_USD'")
