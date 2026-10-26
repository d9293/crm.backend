"""Test fixtures.

Integration tests run against a real PostgreSQL. `backend.md` is explicit that
SQLite and mocks cannot validate the exclusion constraints, row locking and
booking races this system depends on, so there is deliberately no in-memory
fallback — a test that cannot reach PostgreSQL is skipped loudly rather than
silently passing against a different database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _test_database_url() -> str | None:
    """URL of the isolated test database.

    Separate credentials and a separate database from development, per
    INF-00: a test run must be incapable of writing development data.
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit

    password = os.environ.get("TEST_POSTGRES_PASSWORD")
    if not password:
        return None

    host = os.environ.get("TEST_POSTGRES_HOST", "localhost")
    port = os.environ.get("TEST_POSTGRES_PORT", "5433")
    user = os.environ.get("TEST_POSTGRES_USER", "crm_test")
    name = os.environ.get("TEST_POSTGRES_DB", "crm_test")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _test_database_url()
    if url is None:
        pytest.skip(
            "No test database configured. Set TEST_POSTGRES_PASSWORD (and start "
            "the postgres-test profile in crm.infra) to run integration tests."
        )
    return url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Test database unreachable: {type(exc).__name__}")

    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session whose work is always rolled back.

    Each test runs inside a transaction that is discarded afterwards, so tests
    cannot see each other's writes and order cannot affect the result.
    """
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False)
    db = factory()

    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()
