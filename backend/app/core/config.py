"""Single source of runtime configuration.

Every URL, key, limit, rate and threshold is read from the environment / `.env`
(ARCHITECTURE §2 law 7). Invalid or unsafe config fails at startup (law 9: fail closed).
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / ".env"

_PG_SCHEMES = ("postgresql://", "postgres://")


class AppEnv(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


def validate_pg_dsn(value: SecretStr) -> SecretStr:
    raw = value.get_secret_value()
    if not raw.startswith(_PG_SCHEMES):
        raise ValueError("must be a postgresql:// DSN")
    authority = raw.split("://", 1)[1].split("/", 1)[0]
    if authority.count("@") > 1:
        # an unescaped '@' in the password makes the host unparseable
        raise ValueError("DSN has an unescaped '@' — URL-encode the password (@ → %40)")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- runtime ---
    app_env: AppEnv = AppEnv.DEV
    paper_mode: Literal[True] = Field(
        default=True,
        description="Only paper trading exists until the Phase-10 forward-test gate passes.",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)

    # --- auth ---
    api_auth_token: SecretStr

    # --- database ---
    database_url: SecretStr
    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=10, ge=1)
    db_connect_timeout_seconds: float = Field(default=10, gt=0)

    # --- timescale storage (Apache build: chunking + in-process retention job) ---
    ohlcv_chunk_interval_days: int = Field(default=30, ge=1)
    ticks_chunk_interval_hours: int = Field(default=24, ge=1)
    ticks_retention_days: int = Field(default=90, ge=1)
    ticks_retention_cron: str = "20 2 * * *"  # IST; daily, outside market hours

    # --- scheduler ---
    scheduler_misfire_grace_seconds: int = Field(default=60, ge=1)

    # --- cache ---
    cache_max_entries: int = Field(default=10_000, ge=1)

    @field_validator("paper_mode", mode="before")
    @classmethod
    def _paper_mode_locked(cls, value: object) -> object:
        if str(value).strip().lower() not in {"true", "1", "yes"}:
            raise ValueError("PAPER_MODE must be true — live trading is gated behind Phase 10")
        return True

    @field_validator("ticks_retention_cron")
    @classmethod
    def _valid_cron(cls, value: str) -> str:
        from apscheduler.triggers.cron import CronTrigger

        try:
            CronTrigger.from_crontab(value)
        except ValueError as exc:
            raise ValueError(f"invalid crontab expression {value!r}: {exc}") from exc
        return value

    @field_validator("api_auth_token")
    @classmethod
    def _token_strength(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("API_AUTH_TOKEN must be at least 32 characters")
        return value

    @field_validator("database_url")
    @classmethod
    def _dsn_scheme(cls, value: SecretStr) -> SecretStr:
        return validate_pg_dsn(value)

    @model_validator(mode="after")
    def _pool_bounds(self) -> Settings:
        if self.db_pool_max_size < self.db_pool_min_size:
            raise ValueError("DB_POOL_MAX_SIZE must be >= DB_POOL_MIN_SIZE")
        return self

    def secret_values(self) -> list[str]:
        """Raw secret strings, registered with the log masker."""
        return [self.api_auth_token.get_secret_value(), self.database_url.get_secret_value()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # required fields come from env
