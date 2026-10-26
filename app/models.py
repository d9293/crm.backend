"""Cross-cutting tables that exist before any domain does.

Both are required by `development/backend.md` from S1 onward and belong to no
single module, so they live here rather than in a domain package:

- `audit_event` — append-only record of accepted commands. Capture begins in
  S1, not S9; the S9 work is the query UI, not the recording.
- `idempotency_key` — the stored command key, request digest and committed
  response that makes a retried command return its original result instead of
  performing a second one.

Domain tables arrive with their sprints, in their own modules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AuditEvent(Base):
    """Append-only. Never updated, never deleted.

    Enforced in the database by a trigger, not merely by convention — see the
    initial migration. An ORM-level guard would be bypassed by any direct SQL,
    and the point of an audit trail is that it holds even when something else
    has gone wrong.
    """

    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Database time, not application time: the audit trail must not be
    # reorderable by a process with a skewed clock.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Nullable: some events have no authenticated actor, such as a failed
    # sign-in attempt.
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Lets an ACE-scoped reviewer be shown only ACE events.
    location_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Ties an event to the request that caused it, and to the log line.
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Permitted before/after values. Credentials and session material are
    # redacted before they reach this column (BE-20).
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_audit_event_occurred_at", "occurred_at"),
        Index("ix_audit_event_entity", "entity_type", "entity_id"),
        Index("ix_audit_event_actor", "actor_user_id"),
        Index("ix_audit_event_location", "location_id"),
    )


class IdempotencyKey(Base):
    """A committed command, keyed so a retry returns the original result.

    The key is bound to actor, operation and request digest, per
    `README.md` section 8. Binding to the actor stops one client's key from
    returning another's result; binding to the digest makes reuse with
    different input a conflict rather than a silent replay of the wrong thing.
    """

    __tablename__ = "idempotency_key"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    key: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)

    # Hash of the request body. Detects reuse with different input without
    # storing the body itself, which may contain personal data.
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # The uniqueness that makes the mechanism work. A concurrent duplicate
        # loses here, in the database, rather than relying on a check-then-act
        # in application code.
        UniqueConstraint("key", "actor_user_id", "operation", name="uq_idempotency_key_scope"),
        Index("ix_idempotency_key_created_at", "created_at"),
    )
