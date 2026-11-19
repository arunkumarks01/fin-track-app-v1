from __future__ import annotations

from pathlib import Path

import pytest

from app.core.tradingdate import MARKET_TZ
from app.db.migrate import MIGRATIONS_DIR, MigrationError, discover


def _write(d: Path, *names: str) -> None:
    for n in names:
        (d / n).write_text("SELECT 1;", encoding="utf-8")


def test_repo_migrations_are_valid() -> None:
    migs = discover()
    assert migs[0].version == 1
    assert all(len(m.checksum) == 64 for m in migs)


def test_orders_numerically_and_ignores_non_sql(tmp_path: Path) -> None:
    _write(tmp_path, "0002_b.sql", "0001_a.sql", "README.md")
    assert [(m.version, m.name) for m in discover(tmp_path)] == [(1, "a"), (2, "b")]


@pytest.mark.parametrize(
    "names",
    [
        ("0001_a.sql", "0003_c.sql"),  # gap
        ("0002_b.sql",),  # doesn't start at 1
        ("0001_a.sql", "0001_dup.sql"),  # duplicate version
    ],
)
def test_rejects_non_contiguous(tmp_path: Path, names: tuple[str, ...]) -> None:
    _write(tmp_path, *names)
    with pytest.raises(MigrationError, match="contiguously"):
        discover(tmp_path)


@pytest.mark.parametrize("bad", ["1_a.sql", "0001-a.sql", "0001_A.sql", "0001_.sql"])
def test_rejects_bad_filenames(tmp_path: Path, bad: str) -> None:
    _write(tmp_path, bad)
    with pytest.raises(MigrationError, match="bad migration filename"):
        discover(tmp_path)


def test_checksum_tracks_content(tmp_path: Path) -> None:
    _write(tmp_path, "0001_a.sql")
    before = discover(tmp_path)[0].checksum
    (tmp_path / "0001_a.sql").write_text("SELECT 2;", encoding="utf-8")
    assert discover(tmp_path)[0].checksum != before


def test_db_trading_tz_matches_code() -> None:
    """The 1d canonical-ts CHECK and MARKET_TZ must name the same zone."""
    sql = (MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    assert f"AT TIME ZONE '{MARKET_TZ.key}'" in sql
