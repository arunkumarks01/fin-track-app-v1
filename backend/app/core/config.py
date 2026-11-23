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

from app.analytics.parameters import IndicatorPeriods
from app.risk.parameters import PaperMode, RiskParameters, SizingMode
from app.sandbox.parameters import PaperParameters
from app.scoring.parameters import ScoringParameters

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

    paper_submode: PaperMode = "learning"
    paper: PaperParameters = Field(default_factory=PaperParameters)
    risk_sizing_mode: SizingMode = "fixed_risk"
    risk: RiskParameters = Field(default_factory=RiskParameters)

    # --- auth ---
    api_auth_token: SecretStr

    # --- database ---
    database_url: SecretStr
    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=10, ge=1)
    db_connect_timeout_seconds: float = Field(default=10, gt=0)
    # Bound stalled database work and abandoned transactions on every pooled connection.
    db_statement_timeout_ms: int = Field(default=900_000, ge=1)
    db_lock_timeout_ms: int = Field(default=10_000, ge=1)
    db_idle_transaction_timeout_ms: int = Field(default=600_000, ge=1)

    # --- timescale storage (Apache build: chunking + in-process retention job) ---
    ohlcv_chunk_interval_days: int = Field(default=30, ge=1)
    ticks_chunk_interval_hours: int = Field(default=24, ge=1)
    ticks_retention_cron: str = "20 2 * * *"  # IST; the daily Parquet export + verified cleanup
    # --- slice 1d: live tick capture (DEC-1D-TICKS) ---
    kite_ws_url: str = "wss://ws.kite.trade"
    ticks_capture_cron: str = "58 8 * * mon-fri"  # IST, before the 09:00 pre-open
    # capture window (IST): pre-open 09:00 .. post-close 16:00 (covers derivatives to 15:45
    # from 2026-12-07 and the 15:50-16:00 post-close session)
    ticks_capture_start: time = time(9, 0)
    ticks_capture_end: time = time(16, 0)
    ticks_flush_rows: int = Field(default=5000, ge=1)
    ticks_flush_seconds: float = Field(default=2.0, gt=0)
    ticks_buffer_max_rows: int = Field(
        default=500_000, ge=1000
    )  # oldest dropped (and counted) beyond
    ticks_reconnect_backoff_seconds: float = Field(default=2.0, gt=0)
    ticks_reconnect_max_seconds: float = Field(default=60.0, gt=0)
    ticks_idle_timeout_seconds: float = Field(
        default=30.0, gt=0
    )  # Kite sends a heartbeat each second
    ticks_extra_symbols: list[str] = Field(
        default_factory=lambda: ["NSE:INDIA VIX", "NSEIX:GIFT NIFTY"]
    )
    # export: ticks older than this many days are written to Parquet, verified, then dropped
    ticks_hot_days: int = Field(default=7, ge=1)
    ticks_export_batch_rows: int = Field(default=100_000, ge=1000)
    ticks_parquet_dir: Path = Path("data/ticks_parquet")
    # --- slice 1d: option chain snapshots (DEC-1D-CHAIN) ---
    chain_cron: str = "*/5 9-15 * * mon-fri"  # IST; the job itself keeps to the window below
    chain_start: time = time(9, 15)
    chain_end: time = time(15, 30)
    chain_index_underlyings: list[str] = Field(
        default_factory=lambda: ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
    )
    chain_stock_count: int = Field(default=20, ge=0)
    chain_stock_override: list[str] = Field(default_factory=list)  # use these instead of ranking
    chain_rank_sessions: int = Field(default=20, ge=1)
    chain_expiries: int = Field(default=2, ge=1, le=4)
    chain_strikes_each_side: int = Field(default=15, ge=1, le=60)
    chain_quote_batch: int = Field(default=250, ge=1, le=500)
    # the spot instrument of each index underlying (stocks are NSE:<symbol>)
    chain_index_spot: dict[str, str] = Field(
        default_factory=lambda: {
            "NIFTY": "NSE:NIFTY 50",
            "BANKNIFTY": "NSE:NIFTY BANK",
            "FINNIFTY": "NSE:NIFTY FIN SERVICE",
            "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
        }
    )

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
    kite_quote_rps: float = Field(default=1.0, gt=0)  # Kite: 1 req/s for /quote
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
    # Kite publishes a session's official daily close overnight: a 1d bar fetched the same
    # evening is provisional (seen 2026-11-19: 223/224 closes changed by 08:05 next day).
    # Session D's 1d bar is final from D+1 at this IST time (margin left past the 08:05 seen).
    daily_bar_final_next_day_ist: time = time(8, 15)
    daily_ingest_cron: str = "30 8 * * mon-sat"  # IST, after next-day finality
    job_history_size: int = Field(default=20, ge=1, le=200)  # runs kept in memory (Admin page)
    chart_max_bars: int = Field(default=5000, ge=100, le=50_000)  # per /api/data/candles page

    # --- Phase 2 research analytics ---
    shorts_enabled: bool = False
    scoring: ScoringParameters = Field(default_factory=ScoringParameters)
    scoring_news_enabled: bool = False
    scoring_scan_enabled: bool = False
    scoring_swing_morning_cron: str = "20 9 * * mon-fri"
    scoring_swing_close_cron: str = "40 15 * * mon-fri"
    scoring_intraday_cron: str = "0 16 * * mon-fri"

    analytics_periods: IndicatorPeriods = Field(default_factory=IndicatorPeriods)
    analytics_warmup_bars: int = Field(default=800, ge=0, le=5000)
    analytics_max_result_bars: int = Field(default=500, ge=1, le=2000)
    analytics_opening_range_minutes: int = Field(default=15, ge=1, le=120)
    analytics_option_rate_bps: int | None = Field(default=None, ge=-2000, le=5000)
    analytics_option_dividend_bps: int | None = Field(default=None, ge=0, le=5000)
    analytics_option_vol_ceiling: float = Field(default=5.0, gt=0, le=20)
    analytics_option_snapshot_max_age_seconds: int = Field(default=900, ge=1)
    analytics_iv_tenor_days: int = Field(default=30, ge=1, le=90)
    analytics_iv_history_calendar_days: int = Field(default=365, ge=30, le=730)
    analytics_iv_min_observations: int = Field(default=20, ge=2, le=260)

    # --- intraday (Phase 1b, DEC-1B): 1-minute is the single stored source ---
    intraday_floor_date: date = date(2015, 1, 1)  # never request older (Kite's 1m starts ~2015)
    intraday_empty_windows_stop: int = Field(default=3, ge=1)  # consecutive empty → floor
    # Hard cap on fintrack_v2's total size during intraday backfill (fail closed when hit).
    # Budget on the VPS (58 GB free after retiring v1): 35 GB DB + ~3 GB WAL + ~7 GB one local
    # compressed dump + ~13 GB WAL archive / PITR (D24). Lower it if the disk changes.
    intraday_db_budget_gb: float = Field(default=35.0, gt=0)
    intraday_budget_check_every: int = Field(default=20, ge=1)  # windows between size checks
    intraday_chunk_interval_days: int = Field(default=7, ge=1)
    # windows in flight per round (Kite's 3 req/s limit is still enforced by the limiter)
    intraday_concurrency: int = Field(default=4, ge=1, le=16)
    # lost-DB auto-resume (e.g. the dev SSH tunnel resetting): attempts + wait between
    intraday_db_retries: int = Field(default=20, ge=0)
    intraday_db_retry_wait_seconds: float = Field(default=30.0, ge=0)
    intraday_benchmarks: dict[str, str] = Field(
        default_factory=lambda: {"NSE": "NSE:NIFTY 50", "BSE": "BSE:SENSEX"}
    )
    intraday_ingest_cron: str = "40 16 * * mon-fri"  # IST
    # 1m bars are final the same evening (re-fetched next morning: identical, 2026-11-19)
    intraday_final_after_ist: time = time(16, 0)
    # per-instrument session calendar (revised DEC-1B): own norm, persistent re-baseline,
    # market-wide short-day floor (see app/data/session_calendar.py)
    intraday_norm_baseline_sessions: int = Field(default=20, ge=1)
    intraday_norm_step_sessions: int = Field(default=10, ge=2)
    intraday_market_short_share: float = Field(default=0.5, gt=0, le=1)
    intraday_market_min_instruments: int = Field(default=3, ge=1)
    # DEC-2015: intraday analysis (backtests, statistics; Phase 2+) starts here by default. Older
    # 1m (2015: Kite feed holes, ~14-28x the market-wide short days of later years) stays stored
    # and chartable; using it is an explicit opt-in that carries a "2015: feed gaps" badge.
    intraday_analysis_start: date = date(2016, 1, 1)

    # --- NSE archive + measured adjustments (Phase 1c, DEC-B revised) ---
    # The static archive serves an honest client; www.nseindia.com 403s non-browsers and we
    # never impersonate a browser. Only the Bc (corporate actions) and Pd (official prices)
    # members of each day's PR zip are read; zips are never written to disk.
    nse_archive_base_url: str = "https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr"
    nse_user_agent: str = "FinTrack/0.1 (personal research)"
    nse_archive_start: date = date(2015, 1, 1)
    nse_symbol_changes_url: str = (
        "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"
    )
    # the daily job loads the new NSE archive day(s) and re-measures adjustments after its
    # backfill (official cross-check of the bars just stored)
    nse_archive_after_daily: bool = True
    nse_archive_rps: float = Field(default=1.0, gt=0, le=5)
    nse_archive_timeout_seconds: float = Field(default=30.0, gt=0)
    nse_archive_retries: int = Field(default=3, ge=0)
    nse_archive_retry_backoff_seconds: float = Field(default=2.0, ge=0)
    nse_archive_max_zip_mb: float = Field(default=20.0, gt=0)  # largest seen: 0.66 MB
    nse_archive_max_member_mb: float = Field(default=20.0, gt=0)  # zip-bomb guard
    # --- Phase 1e reference data (DEC-1E-SOURCES) from the same NSE archive host ---
    # delivery position: <base>/MTO_ddmmyyyy.DAT; participant OI/volume: <base>/fao_participant_*
    nse_mto_base_url: str = "https://nsearchives.nseindia.com/archives/equities/mto"
    nse_participant_base_url: str = "https://nsearchives.nseindia.com/content/nsccl"
    # --- news headlines (publisher RSS; headline, link, summary only) ---
    news_feeds: dict[str, str] = Field(
        default_factory=lambda: {
            "business_standard_markets": "https://www.business-standard.com/rss/markets-106.rss",
            "mint_markets": "https://www.livemint.com/rss/markets",
            "et_markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
            "rbi_press": "https://www.rbi.org.in/pressreleases_rss.xml",
            "sebi": "https://www.sebi.gov.in/sebirss.xml",
        }
    )
    news_fetch_cron: str = "7,22,37,52 * * * *"  # IST, every 15 minutes
    news_user_agent: str = "FinTrack/0.1 (personal research)"
    news_timeout_seconds: float = Field(default=20.0, gt=0)
    news_max_bytes: int = Field(default=2_000_000, gt=0)
    news_max_items_per_feed: int = Field(default=200, ge=1)
    # --- FRED (St. Louis Fed) daily series; off until FRED_API_KEY is set (user-created) ---
    fred_api_key: SecretStr | None = None
    fred_base_url: str = "https://api.stlouisfed.org/fred"
    fred_start: date = date(2015, 1, 1)
    # series id → (title, unit, scale): value stored as value * 10^scale, exact or rejected
    fred_series: dict[str, tuple[str, str, int]] = Field(
        default_factory=lambda: {
            "SP500": ("S&P 500", "index points", 2),
            "NASDAQCOM": ("NASDAQ Composite", "index points", 2),
            "DJIA": ("Dow Jones Industrial Average", "index points", 2),
            "VIXCLS": ("CBOE VIX", "index points", 2),
            "DCOILBRENTEU": ("Brent crude", "USD per barrel", 2),
            "DGS10": ("US 10-year Treasury yield", "percent", 2),
            "DEXINUS": ("INR per USD (noon buying rate)", "INR", 4),
        }
    )
    # --- slice 1f: F&O history (NSE derivatives bhavcopy) ---
    # old format (to 2024): <base>/<YYYY>/<MON>/fo<DD><MON><YYYY>bhav.csv.zip
    nse_fo_old_base_url: str = "https://nsearchives.nseindia.com/content/historical/DERIVATIVES"
    # UDiFF format: <base>/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip
    nse_fo_new_base_url: str = "https://nsearchives.nseindia.com/content/fo"
    # first date the UDiFF file was verified to exist (both formats overlap through mid-2024)
    fo_udiff_from: date = date(2024, 1, 2)
    # the F&O history stops when the database reaches this size (it is the biggest table)
    fo_db_budget_gb: float = Field(default=35.0, gt=0)
    # bulk.csv / block.csv: the latest session only (captured forward)
    nse_deals_base_url: str = "https://nsearchives.nseindia.com/content/equities"
    # after each daily run (and its NSE archive load), fetch the new days' reference files too
    reference_after_daily: bool = False
    # NSE's participant OI files are often off by 1-2 contracts between the participants' sum
    # and its own TOTAL (rounding; measured 2026-11-21). Within this, stored as given and the gap
    # recorded; beyond it, the day is refused.
    participant_total_tolerance_contracts: int = Field(default=2, ge=0, le=100)
    # a day's Kite price agrees with NSE * factor within this many paisa (both sides rounded)
    adj_tolerance_paisa: float = Field(default=2.5, gt=0)  # half a 5-paisa tick
    # windows with fewer measured days are under-determined: flagged, no as-traded derived
    adj_min_window_days: int = Field(default=5, ge=2)
    # 1m/daily grain: 2 of (open, high, low) ratios must agree within this fraction
    adj_grain_agree: float = Field(default=0.002, gt=0, lt=0.1)
    # a window-to-window step smaller than this (fraction) is no jump at all
    adj_flat: float = Field(default=0.0005, gt=0, lt=0.05)
    # a measured jump matches an NSE action's expected ratio within this fraction
    adj_match: float = Field(default=0.005, gt=0, lt=0.1)
    # unexplained jumps up to this fraction are badged; larger ones make both windows suspect
    adj_unexplained_small: float = Field(default=0.03, gt=0, lt=0.5)
    # DEC-CORROB: an unlisted step is 'corroborated' only at a clean ratio p/q with p, q ≤ this
    adj_corroborate_max_term: int = Field(default=10, ge=2, le=50)
    # symbols measured at once (each its own DB connection + transaction); keep below the pool size
    adj_analysis_concurrency: int = Field(default=4, ge=1, le=16)
    # the Admin review list shows unexplained steps of at least this size (fraction); smaller
    # ones are refit noise — counted, still on each symbol's page (measured: median 0.03%)
    adj_review_min_step: float = Field(default=0.0025, gt=0, lt=0.5)

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
    # A symbol's listing starts its last Kite segment after a gap of at least this many NSE
    # trading days in which NSE ALSO shows no trading for it (ghost pre-listing bars, e.g.
    # SBICARD's 20 bars in Jan 2015 vs its 2020-03-16 listing). Unverifiable gaps (no archive)
    # never reset the listing — they stay counted as missing.
    coverage_listing_gap_sessions: int = Field(default=60, ge=5)

    # --- cache ---
    cache_max_entries: int = Field(default=10_000, ge=1)

    @field_validator("paper_mode", mode="before")
    @classmethod
    def _paper_mode_locked(cls, value: object) -> object:
        if str(value).strip().lower() not in {"true", "1", "yes"}:
            raise ValueError("PAPER_MODE must be true — live trading is gated behind Phase 10")
        return True

    @field_validator(
        "ticks_retention_cron",
        "daily_ingest_cron",
        "intraday_ingest_cron",
        "news_fetch_cron",
        "ticks_capture_cron",
        "chain_cron",
        "scoring_swing_morning_cron",
        "scoring_swing_close_cron",
        "scoring_intraday_cron",
    )
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
        if self.adj_analysis_concurrency >= self.db_pool_max_size:
            raise ValueError(
                "ADJ_ANALYSIS_CONCURRENCY must be below DB_POOL_MAX_SIZE "
                "(pages need connections too)"
            )
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
        for s in (self.kite_api_key, self.kite_api_secret, self.fred_api_key):
            if s is not None:
                values.append(s.get_secret_value())
        return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # required fields come from env
