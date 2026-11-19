"""Single source of runtime configuration.

Every URL, key, limit, rate and threshold is read from the environment / `.env`
(ARCHITECTURE §2 law 7). Invalid or unsafe config fails at startup (law 9: fail closed).
"""

from __future__ import annotations

from datetime import date, time
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

    # --- Kite Connect (optional: without credentials the app boots, Kite features fail closed) ---
    kite_api_key: SecretStr | None = None
    kite_api_secret: SecretStr | None = None
    kite_api_base_url: str = "https://api.kite.trade"
    kite_login_url: str = "https://kite.zerodha.com/connect/login"
    kite_timeout_seconds: float = Field(default=15, gt=0)
    kite_historical_rps: float = Field(default=3.0, gt=0)  # Kite: 3 req/s historical
    kite_default_rps: float = Field(default=10.0, gt=0)  # Kite: 10 req/s other endpoints
    kite_max_retries: int = Field(default=3, ge=0)
    kite_retry_backoff_seconds: float = Field(default=1.0, ge=0)
    kite_session_expiry_hour_ist: int = Field(default=6, ge=0, le=23)  # regulatory 6 AM
    # max days per historical request (Kite doesn't publish these; v1-proven, DEC-K2)
    kite_window_days: dict[str, int] = Field(
        default_factory=lambda: {
            "minute": 60,
            "3minute": 60,
            "5minute": 100,
            "10minute": 100,
            "15minute": 200,
            "30minute": 200,
            "60minute": 400,
            "day": 2000,
        }
    )

    # --- universe + ingestion ---
    universe_index_symbols: list[str] = Field(
        default_factory=lambda: [
            "NSE:NIFTY 50",
            "NSE:NIFTY BANK",
            "NSE:NIFTY FIN SERVICE",
            "NSE:NIFTY MID SELECT",
            "BSE:SENSEX",
            "BSE:BANKEX",
        ]
    )
    ingest_daily_start: date = date(2015, 1, 1)
    daily_bar_final_after_ist: time = time(16, 0)  # today's 1d bar is partial before this
    daily_ingest_cron: str = "15 16 * * 1-5"  # IST

    # --- validation gate ---
    validation_large_gap_pct: int = Field(default=20, ge=1)  # open vs prev close
    validation_missing_weekdays_flag: int = Field(default=3, ge=1)
    validation_index_max_gap_pct: int = Field(default=20, ge=1)  # indices: quarantine above

    # --- coverage gate (BUILD_PLAN Phase 1) ---
    coverage_min_clean_daily_bars: int = Field(default=250, ge=1)
    coverage_min_symbol_pct: int = Field(default=95, ge=1, le=100)
    coverage_index_oldest_by: date = date(2015, 1, 31)
    coverage_max_stale_weekdays: int = Field(default=3, ge=0)
    # weekdays without a bar between a symbol's first and last bar (holidays alone ≈ 5%)
    coverage_max_weekday_gap_pct: float = Field(default=10.0, gt=0, le=100)

    # --- cache ---
    cache_max_entries: int = Field(default=10_000, ge=1)

    @field_validator("paper_mode", mode="before")
    @classmethod
    def _paper_mode_locked(cls, value: object) -> object:
        if str(value).strip().lower() not in {"true", "1", "yes"}:
            raise ValueError("PAPER_MODE must be true — live trading is gated behind Phase 10")
        return True

    @field_validator("ticks_retention_cron", "daily_ingest_cron")
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

    @field_validator("universe_index_symbols")
    @classmethod
    def _qualified_symbols(cls, value: list[str]) -> list[str]:
        bad = [v for v in value if ":" not in v or not v.split(":", 1)[1]]
        if bad:
            raise ValueError(f"index symbols must be 'EXCHANGE:TRADINGSYMBOL', got {bad}")
        return value

    @property
    def kite_configured(self) -> bool:
        return self.kite_api_key is not None and self.kite_api_secret is not None

    def secret_values(self) -> list[str]:
        """Raw secret strings, registered with the log masker."""
        values = [self.api_auth_token.get_secret_value(), self.database_url.get_secret_value()]
        for s in (self.kite_api_key, self.kite_api_secret):
            if s is not None:
                values.append(s.get_secret_value())
        return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # required fields come from env
