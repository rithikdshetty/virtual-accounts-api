"""initial schema: accounts, ledger, idempotency, webhooks, events

Revision ID: 0001
Revises:
Create Date: 2026-05-21

The double-entry ledger is the heart of the system. Every monetary
movement produces paired entries that net to zero. The invariant
SUM(amount) = 0 across the entire ledger_entries table (when including
the FBO cash counter-entries) must hold at all times.

Design choices documented in NOTES.md and the ADRs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------- accounts ----------
    # One row per virtual account. Status is an enum-like string column
    # rather than a Postgres ENUM because adding new status values to a
    # Postgres ENUM requires DDL (slow, locks). Plain text + CHECK
    # constraint is more flexible.
    op.create_table(
        "accounts",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("customer_id", sa.Text, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("livemode", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("metadata_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'frozen', 'closed')",
            name="accounts_status_check",
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="accounts_currency_iso4217"
        ),
    )
    op.create_index("idx_accounts_customer_id", "accounts", ["customer_id"])
    op.create_index("idx_accounts_status", "accounts", ["status"])

    # ---------- ledger_entries ----------
    # The actual ledger. Every monetary movement is one or more rows here.
    # Amount is signed minor units: positive credits the account, negative
    # debits. A transfer of 100 USD from A to B produces two rows:
    #   (account_id=A, amount=-10000, ...)
    #   (account_id=B, amount=+10000, ...)
    # Both linked by the same related_transfer_id.
    #
    # The FBO cash position is account_id='fbo_cash_USD' (or per currency).
    # A deposit creates: (fbo_cash_USD, +10000) and (acct_xxx, +10000).
    # A withdrawal creates: (fbo_cash_USD, -10000) and (acct_xxx, -10000).
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("entry_type", sa.Text, nullable=False),
        sa.Column("related_transfer_id", sa.Text, nullable=True),
        sa.Column("related_deposit_id", sa.Text, nullable=True),
        sa.Column("related_withdrawal_id", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "posted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "entry_type IN ('deposit', 'withdrawal', 'transfer_in', "
            "'transfer_out', 'reversal', 'fbo_cash')",
            name="ledger_entries_type_check",
        ),
        sa.CheckConstraint("amount != 0", name="ledger_entries_amount_nonzero"),
    )
    op.create_index(
        "idx_ledger_account_posted",
        "ledger_entries",
        ["account_id", "posted_at"],
    )
    op.create_index(
        "idx_ledger_transfer_id", "ledger_entries", ["related_transfer_id"]
    )

    # ---------- transfers ----------
    # Internal movements between two virtual accounts. The ledger entries
    # are the source of truth for the money; this table is the "header"
    # describing the business event.
    op.create_table(
        "transfers",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("source_account_id", sa.Text, nullable=False),
        sa.Column("destination_account_id", sa.Text, nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("livemode", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("reverses_transfer_id", sa.Text, nullable=True),
        sa.Column("failure_code", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("metadata_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("posted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('posted', 'failed', 'reversed')",
            name="transfers_status_check",
        ),
        sa.CheckConstraint(
            "source_account_id != destination_account_id",
            name="transfers_no_self",
        ),
        sa.CheckConstraint("amount > 0", name="transfers_amount_positive"),
    )
    op.create_index(
        "idx_transfers_source", "transfers", ["source_account_id"]
    )
    op.create_index(
        "idx_transfers_destination", "transfers", ["destination_account_id"]
    )

    # ---------- deposits ----------
    op.create_table(
        "deposits",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("livemode", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("rail", sa.Text, nullable=False),
        sa.Column("source_reference", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("posted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'posted', 'failed')",
            name="deposits_status_check",
        ),
        sa.CheckConstraint(
            "rail IN ('ach', 'wire', 'rtp', 'internal_test')",
            name="deposits_rail_check",
        ),
        sa.CheckConstraint("amount > 0", name="deposits_amount_positive"),
    )
    op.create_index("idx_deposits_account", "deposits", ["account_id"])

    # ---------- withdrawals ----------
    op.create_table(
        "withdrawals",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("livemode", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("rail", sa.Text, nullable=False),
        sa.Column("destination_reference", sa.Text, nullable=False),
        sa.Column("failure_code", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("posted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'posted', 'failed')",
            name="withdrawals_status_check",
        ),
        sa.CheckConstraint(
            "rail IN ('ach', 'wire', 'rtp', 'internal_test')",
            name="withdrawals_rail_check",
        ),
        sa.CheckConstraint("amount > 0", name="withdrawals_amount_positive"),
    )
    op.create_index("idx_withdrawals_account", "withdrawals", ["account_id"])

    # ---------- idempotency_keys ----------
    # Stores the request hash + response body keyed by (api_key, key).
    # Same key + same body within 24h returns cached response.
    # Same key + different body returns 409.
    # Expires after 24h via a scheduled cleanup job (not in v0.1).
    op.create_table(
        "idempotency_keys",
        sa.Column("api_key_hash", sa.Text, nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("request_hash", sa.Text, nullable=False),
        sa.Column("response_status", sa.Integer, nullable=False),
        sa.Column("response_body", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW() + INTERVAL '24 hours'"),
        ),
        sa.PrimaryKeyConstraint("api_key_hash", "idempotency_key"),
    )
    op.create_index(
        "idx_idempotency_expires", "idempotency_keys", ["expires_at"]
    )

    # ---------- webhook_endpoints ----------
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("event_types", sa.JSON, nullable=False),
        sa.Column("secret_hash", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="webhook_endpoints_status_check",
        ),
    )

    # ---------- events ----------
    # Every state change writes to this table. Webhooks fan out from here.
    # /events endpoint reads from here.
    op.create_table(
        "events",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("livemode", sa.Boolean, nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_events_type_created", "events", ["event_type", "created_at"])
    op.create_index("idx_events_created", "events", ["created_at"])

    # ---------- webhook_deliveries ----------
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("endpoint_id", sa.Text, nullable=False),
        sa.Column("event_id", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("response_status", sa.Integer, nullable=True),
        sa.Column("response_body_snippet", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'dead_lettered')",
            name="webhook_deliveries_status_check",
        ),
    )
    op.create_index(
        "idx_deliveries_next_attempt",
        "webhook_deliveries",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("events")
    op.drop_table("webhook_endpoints")
    op.drop_table("idempotency_keys")
    op.drop_table("withdrawals")
    op.drop_table("deposits")
    op.drop_table("transfers")
    op.drop_table("ledger_entries")
    op.drop_table("accounts")
