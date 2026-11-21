from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import AppEnv, Settings
from tests.conftest import SettingsFactory

REQUIRED_ENV = ("API_AUTH_TOKEN", "DATABASE_URL")


def test_defaults_are_paper_and_valid(make_settings: SettingsFactory) -> None:
    s = make_settings()
    assert s.paper_mode is True
    assert s.app_env is AppEnv.TEST


def test_values_come_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_AUTH_TOKEN", "e" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@h:1/db")
    monkeypatch.setenv("TICKS_RETENTION_DAYS", "45")
    s = Settings(_env_file=None)
    assert s.ticks_retention_days == 45
    assert s.api_auth_token.get_secret_value() == "e" * 32


@pytest.mark.parametrize("missing", REQUIRED_ENV)
def test_missing_required_value_fails_startup(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv("API_AUTH_TOKEN", "e" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@h:1/db")
    monkeypatch.delenv(missing)
    with pytest.raises(ValidationError, match=missing.lower()):
        Settings(_env_file=None)


@pytest.mark.parametrize("value", ["false", "0", "no", "False", ""])
def test_paper_mode_cannot_be_disabled(make_settings: SettingsFactory, value: str) -> None:
    with pytest.raises(ValidationError, match="PAPER_MODE must be true"):
        make_settings(paper_mode=value)


def test_short_auth_token_rejected(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        make_settings(api_auth_token="short")


def test_non_postgres_dsn_rejected(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="postgresql"):
        make_settings(database_url="mysql://x@y/z")


def test_unescaped_at_in_dsn_password_rejected(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="URL-encode the password"):
        make_settings(database_url="postgresql://u:p@ss@localhost:5433/db")
    make_settings(database_url="postgresql://u:p%40ss@localhost:5433/db")  # encoded is fine


def test_pool_bounds_enforced(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="DB_POOL_MAX_SIZE"):
        make_settings(db_pool_min_size=5, db_pool_max_size=2)


def test_secrets_hidden_in_repr(make_settings: SettingsFactory) -> None:
    s = make_settings()
    assert "t" * 40 not in repr(s)
    assert "u:p@" not in repr(s)
    assert set(s.secret_values()) == {"t" * 40, "postgresql://u:p@localhost:5432/unused_test"}


def test_settings_are_immutable(make_settings: SettingsFactory) -> None:
    s = make_settings()
    with pytest.raises(ValidationError):
        s.paper_mode = False  # type: ignore[misc,assignment]


def test_dec_2015_and_corroboration_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date

    monkeypatch.setenv("API_AUTH_TOKEN", "e" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@h:1/db")
    s = Settings(_env_file=None)
    assert s.intraday_analysis_start == date(2016, 1, 1)  # DEC-2015
    assert s.adj_corroborate_max_term == 10  # DEC-CORROB
    monkeypatch.setenv("INTRADAY_ANALYSIS_START", "2015-01-09")  # an explicit opt-in
    assert Settings(_env_file=None).intraday_analysis_start == date(2015, 1, 9)


def test_corroboration_term_bounds(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError):
        make_settings(adj_corroborate_max_term=1)  # p/q with p, q <= 1 is only 1/1
    with pytest.raises(ValidationError):
        make_settings(intraday_analysis_start="2016-13-01")
