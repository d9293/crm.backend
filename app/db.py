"""Database engine, session factory and the declarative base.

Conventions set here that the whole schema inherits:

- **Timezone-aware instants only.** `TIMESTAMP WITH TIME ZONE` everywhere, per
  `README.md` section 6. A naive timestamp column is how a booking ends up an
  hour wrong after a timezone change.
- **Named constraints.** A naming convention means Alembic generates stable,
  readable constraint names, so a migration that drops a constraint does not
  have to guess what PostgreSQL called it.
- **No autoflush.** Implicit flushes make it hard to reason about when a
  write actually happens, which matters where the ordering of locks and
  inserts is the correctness argument (BE-06).
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

# Alembic uses these templates to name generated constraints.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_db_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    return create_engine(
        url or settings.database_url,
        # Verifies a pooled connection before use. Without it, a connection
        # dropped by a restart or an idle timeout surfaces as a failed request
        # rather than being transparently replaced.
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        # Never log the URL: it contains the password.
        echo=False,
    )


_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Process-wide engine, created on first use.

    Lazy rather than created at import time, so importing this module does not
    require a reachable database — tests and `alembic` both import it before
    any connection is wanted.
    """
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _SessionFactory


def get_session() -> Iterator[Session]:
    """FastAPI dependency. One session per request, rolled back on failure.

    The rollback is explicit rather than relying on the context manager, so a
    partially applied transaction can never be committed by a later handler
    reusing the connection.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
