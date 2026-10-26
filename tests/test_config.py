"""Configuration safety.

These do not need a database. They cover the rules that exist so a
misconfiguration fails at boot rather than in production.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

# Variables Settings reads from the environment. Tests that assert on defaults
# must not see whatever the developer happens to have exported.
_ENV_VARS = (
    "ENVIRONMENT",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "APP_SECRET_KEY",
    "SESSION_COOKIE_SECURE",
    "ALLOWED_ORIGINS",
    "BUSINESS_TIMEZONE",
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove ambient configuration before every test in this module.

    Without this, `test_required_secrets_have_no_default` passes in CI and
    fails on a developer machine that has exported POSTGRES_PASSWORD — or,
    worse, silently passes for the wrong reason. Pydantic settings read the
    process environment by design, so the test has to control it.
    """
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def base_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "postgres_db": "crm_test",
        "postgres_user": "crm_test",
        "postgres_password": "not-a-real-password",
        "app_secret_key": "not-a-real-secret",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_required_secrets_have_no_default() -> None:
    # Missing password and secret key must stop the process, not fall back to
    # an empty string that happens to authenticate against a misconfigured
    # database.
    with pytest.raises(ValidationError):
        Settings(postgres_db="crm", postgres_user="crm")  # type: ignore[call-arg]


def test_secrets_are_not_in_the_repr() -> None:
    settings = base_settings()
    rendered = repr(settings)

    # A stray print, or a traceback rendering locals, must not leak these.
    assert "not-a-real-password" not in rendered
    assert "not-a-real-secret" not in rendered
    assert "<redacted>" in rendered
    # str() is used by f-strings and logging, so it is covered too.
    assert "not-a-real-password" not in str(settings)


def test_database_url_is_assembled_from_parts() -> None:
    settings = base_settings(postgres_host="db", postgres_port=5432)
    assert settings.database_url.startswith("postgresql+psycopg://crm_test:")
    assert settings.database_url.endswith("@db:5432/crm_test")


def test_production_rejects_an_insecure_session_cookie() -> None:
    # The flag exists because local development runs without TLS. It must not
    # be possible to ship that by forgetting one variable.
    with pytest.raises(ValidationError):
        base_settings(environment="production", session_cookie_secure=False)


def test_production_accepts_a_secure_session_cookie() -> None:
    settings = base_settings(environment="production", session_cookie_secure=True)
    assert settings.session_cookie_secure is True


def test_allowed_origins_accepts_a_comma_separated_string() -> None:
    # This is how a .env file supplies a list.
    settings = base_settings(allowed_origins="http://a.test, http://b.test")
    assert settings.allowed_origins == ["http://a.test", "http://b.test"]


def test_business_timezone_is_an_iana_identifier() -> None:
    # Never a fixed offset: Asia/Almaty moved from UTC+6 to UTC+5 in 2024, and
    # instants before that are still +06.
    settings = base_settings()
    assert settings.business_timezone == "Asia/Almaty"
    assert "+0" not in settings.business_timezone


def test_allowed_origins_parses_from_an_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: this path failed under Compose but not locally.

    pydantic-settings JSON-decodes list fields read from the environment
    before any validator runs, so a plain comma-separated value raised a
    SettingsError at boot. The earlier test passed a Python value directly and
    never exercised the environment source — the only place the bug lived.
    """
    monkeypatch.setenv("POSTGRES_DB", "crm")
    monkeypatch.setenv("POSTGRES_USER", "crm")
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("APP_SECRET_KEY", "y")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173,https://crm.example")

    settings = Settings()  # type: ignore[call-arg]

    assert settings.allowed_origins == ["http://localhost:5173", "https://crm.example"]


def test_allowed_origins_accepts_a_single_origin_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The exact value the Compose default supplies.
    monkeypatch.setenv("POSTGRES_DB", "crm")
    monkeypatch.setenv("POSTGRES_USER", "crm")
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("APP_SECRET_KEY", "y")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173")

    settings = Settings()  # type: ignore[call-arg]

    assert settings.allowed_origins == ["http://localhost:5173"]
