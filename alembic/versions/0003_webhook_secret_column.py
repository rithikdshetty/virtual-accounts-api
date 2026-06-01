"""add raw secret column to webhook_endpoints

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-22

We need the raw secret server-side to sign outbound webhook payloads.
The previous design stored only the hash, which works for auth-style
verification but not for HMAC signing where the server is the signer.

For v0.1 we store the raw secret as TEXT. Production should encrypt
this column at rest using a KMS-managed key. NOTES.md documents this.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "webhook_endpoints",
        sa.Column("secret", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("webhook_endpoints", "secret")
