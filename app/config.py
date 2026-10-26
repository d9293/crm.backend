"""Application configuration, read from the environment.

Two rules from `development/infra.md` shape this module:

- Required secrets have **no default**. The application refuses to start
  without them rather than falling back to something insecure, so a
  misconfigured deployment fails loudly at boot instead of quietly running
  with a known password.
- Nothing here is logged. `Settings.__repr__` is overridden so an accidental
  `print(settings)` or an exception rendering locals cannot leak the database
  password or session key.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- environment ------------------------------------------------------
    environment: Literal["development", "test", "production"] = "development"

    # --- database ---------------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str
    postgres_user: str
    # REQUIRED: no default. An empty password must never silently work.
    postgres_password: SecretStr

    # --- session and CSRF -------------------------------------------------
    # REQUIRED: no default, and rotated at handover per INF-08.
    app_secret_key: SecretStr

    session_cookie_name: str = "crm_session"
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # --- business ---------------------------------------------------------
    # The IANA identifier, never a fixed offset: Asia/Almaty moved from UTC+6
    # to UTC+5 on 1 March 2024, and historical instants are still +06.
    business_timezone: str = "Asia/Almaty"

    # `NoDecode` is load-bearing. Without it pydantic-settings tries to
    # JSON-decode a list field read from the environment *before* any
    # validator runs, so ALLOWED_ORIGINS=http://localhost:5173 raises a
    # SettingsError at boot. NoDecode hands the raw string to the validator
    # below instead.
    #
    # This failed only under Compose, where the variable is actually set —
    # local runs relied on the default and never exercised the path.
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, as an env var or .env file supplies it."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("session_cookie_secure")
    @classmethod
    def secure_cookie_in_production(cls, value: bool, info: object) -> bool:
        """A production deployment may not serve session cookies over plain HTTP.

        Local development legitimately runs without TLS, so the flag exists —
        but it must not be possible to ship that configuration by forgetting
        to change one variable.
        """
        data = getattr(info, "data", {})
        if data.get("environment") == "production" and not value:
            raise ValueError("session_cookie_secure must be true when environment is production")
        return value

    @property
    def database_url(self) -> str:
        """SQLAlchemy URL. Contains the password, so it is never logged."""
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def __repr__(self) -> str:
        """Redacted. Prevents a stray print or traceback from leaking secrets."""
        return (
            f"Settings(environment={self.environment!r}, "
            f"postgres_db={self.postgres_db!r}, "
            f"postgres_host={self.postgres_host!r}, "
            "postgres_password=<redacted>, app_secret_key=<redacted>)"
        )

    __str__ = __repr__


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached so the environment is read once per process."""
    return Settings()  # type: ignore[call-arg]
