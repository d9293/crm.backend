"""Initial schema: audit events and idempotency keys

Revision ID: 0001_initial
Revises:
Created: 2026-10-26

Establishes the two cross-cutting tables that exist before any domain, and
enforces the append-only property of the audit trail in the database rather
than by convention.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Required by the BE-06 exclusion constraint, which combines scalar
    # resource identity with a timezone-aware range. db/init in crm.infra
    # creates it for a fresh container, but a database restored from a dump or
    # provisioned another way may not have it — so the migration is the
    # authoritative place and must be idempotent about it.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "audit_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=60), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("location_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audit_event"),
    )
    op.create_index("ix_audit_event_occurred_at", "audit_event", ["occurred_at"])
    op.create_index("ix_audit_event_entity", "audit_event", ["entity_type", "entity_id"])
    op.create_index("ix_audit_event_actor", "audit_event", ["actor_user_id"])
    op.create_index("ix_audit_event_location", "audit_event", ["location_id"])

    # Append-only, enforced by the database.
    #
    # `backend.md` requires audit events to be append-only and correction to
    # happen through linked entries rather than destructive edits. An
    # application-level guard would be bypassed by any direct SQL — a
    # migration, a maintenance script, or someone with psql — and an audit
    # trail that can be quietly rewritten is not evidence of anything.
    #
    # Deliberately refuses TRUNCATE as well: it is neither UPDATE nor DELETE
    # and would otherwise empty the table without tripping the trigger.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_event_is_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_event is append-only: % is not permitted', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_event_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_event
        FOR EACH ROW EXECUTE FUNCTION audit_event_is_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_event_no_truncate
        BEFORE TRUNCATE ON audit_event
        FOR STATEMENT EXECUTE FUNCTION audit_event_is_append_only();
        """
    )

    op.create_table(
        "idempotency_key",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_key"),
        # The constraint that makes idempotency work under concurrency: a
        # duplicate loses here, in the database, rather than depending on a
        # check-then-insert race in application code.
        sa.UniqueConstraint("key", "actor_user_id", "operation", name="uq_idempotency_key_scope"),
    )
    op.create_index("ix_idempotency_key_created_at", "idempotency_key", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_key_created_at", table_name="idempotency_key")
    op.drop_table("idempotency_key")

    op.execute("DROP TRIGGER IF EXISTS audit_event_no_truncate ON audit_event")
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_update_delete ON audit_event")
    op.execute("DROP FUNCTION IF EXISTS audit_event_is_append_only()")

    op.drop_index("ix_audit_event_location", table_name="audit_event")
    op.drop_index("ix_audit_event_actor", table_name="audit_event")
    op.drop_index("ix_audit_event_entity", table_name="audit_event")
    op.drop_index("ix_audit_event_occurred_at", table_name="audit_event")
    op.drop_table("audit_event")

    # btree_gist is deliberately not dropped: other schema may depend on it,
    # and dropping an extension is not the business of a table migration.
