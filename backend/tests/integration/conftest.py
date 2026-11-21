"""Integration fixtures against a real TimescaleDB.

Safety guards (fail closed):
- the target database name MUST end in `_test` — anything else aborts the whole run;
- with REQUIRE_DB_TESTS=1 (CI) a missing TEST_DATABASE_URL is a hard failure, not a skip.
Each test starts from an empty `app` schema (dropped + re-migrated).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import asyncpg
import pytest
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import ENV_FILE, Settings, validate_pg_dsn
from tests.conftest import build_settings


class _TestDb(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")
    test_database_url: SecretStr | None = None

    @field_validator("test_database_url")
    @classmethod
    def _scheme(cls, value: SecretStr | None) -> SecretStr | None:
        return None if value is None else validate_pg_dsn(value)


def _test_dsn() -> str:
    dsn = _TestDb().test_database_url
    if dsn is None:
        if os.environ.get("REQUIRE_DB_TESTS") == "1":
            pytest.fail("REQUIRE_DB_TESTS=1 but TEST_DATABASE_URL is not set")
        pytest.skip("TEST_DATABASE_URL not set — integration tests skipped")
    raw = dsn.get_secret_value()
    dbname = urlparse(raw).path.lstrip("/")
    if not dbname.endswith("_test"):
        pytest.exit(f"refusing to run destructive tests against non-test database {dbname!r}")
    return raw


@pytest.fixture
def test_dsn() -> str:
    return _test_dsn()


@pytest.fixture
def db_settings(test_dsn: str) -> Settings:
    return build_settings(database_url=test_dsn, db_pool_max_size=2, adj_analysis_concurrency=1)


async def reset_schema(conn: Any) -> None:
    await conn.execute("DROP SCHEMA IF EXISTS app CASCADE")


@pytest.fixture
async def db(test_dsn: str) -> AsyncIterator[Any]:
    conn = await asyncpg.connect(test_dsn, server_settings={"timezone": "UTC"})
    try:
        await reset_schema(conn)
        yield conn
    finally:
        await conn.close()
