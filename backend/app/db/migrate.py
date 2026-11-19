"""Ordered, checksummed SQL migrations (no ORM).

Rules, all fail-closed:
- files are `NNNN_snake_name.sql`, numbered contiguously from 0001;
- each pending file runs in its own transaction and is recorded in `app.applied_migrations`;
- an applied file whose content changed (checksum mismatch) aborts startup;
- a DB that has versions the code doesn't know about (DB ahead of code) aborts startup;
- the TimescaleDB extension must already exist (created once by a superuser).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
SCHEMA = "app"
_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")

_BOOTSTRAP = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {SCHEMA}.applied_migrations (
    version     integer     PRIMARY KEY,
    name        text        NOT NULL,
    checksum    text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(directory.iterdir()):
        if path.suffix != ".sql":
            continue
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(f"bad migration filename: {path.name}")
        migrations.append(
            Migration(int(match["version"]), match["name"], path.read_text(encoding="utf-8"))
        )
    expected = list(range(1, len(migrations) + 1))
    if [m.version for m in migrations] != expected:
        raise MigrationError(
            f"migrations must be numbered contiguously from 0001, got "
            f"{[m.version for m in migrations]}"
        )
    return migrations


async def _require_timescaledb(conn: Any) -> str:
    version = await conn.fetchval(
        "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
    )
    if version is None:
        raise MigrationError(
            "timescaledb extension is not installed in this database — "
            "run `CREATE EXTENSION timescaledb;` as a superuser first"
        )
    return str(version)


async def migrate(conn: Any, migrations: list[Migration] | None = None) -> list[int]:
    """Apply pending migrations in order. Returns the versions applied by this call."""
    migrations = discover() if migrations is None else migrations
    ts_version = await _require_timescaledb(conn)
    await conn.execute(_BOOTSTRAP)

    rows = await conn.fetch(
        f"SELECT version, name, checksum FROM {SCHEMA}.applied_migrations ORDER BY version"  # noqa: S608
    )
    known = {m.version: m for m in migrations}
    applied_versions = [r["version"] for r in rows]
    for row in rows:
        mig = known.get(row["version"])
        if mig is None:
            raise MigrationError(
                f"database has migration {row['version']} ({row['name']}) unknown to this code"
            )
        if mig.checksum != row["checksum"]:
            raise MigrationError(
                f"migration {mig.version:04d}_{mig.name} was modified after being applied"
            )
    if applied_versions != list(range(1, len(applied_versions) + 1)):
        raise MigrationError(f"applied migrations are not contiguous: {applied_versions}")

    already = set(applied_versions)
    applied_now: list[int] = []
    for mig in migrations:
        if mig.version in already:
            continue
        async with conn.transaction():
            await conn.execute(mig.sql)
            await conn.execute(
                f"INSERT INTO {SCHEMA}.applied_migrations (version, name, checksum) "  # noqa: S608
                "VALUES ($1, $2, $3)",
                mig.version,
                mig.name,
                mig.checksum,
            )
        log.info("applied migration %04d_%s", mig.version, mig.name)
        applied_now.append(mig.version)
    log.info(
        "schema at version %d (timescaledb %s)",
        len(applied_versions) + len(applied_now),
        ts_version,
    )
    return applied_now
