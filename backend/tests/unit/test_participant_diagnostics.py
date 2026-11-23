from datetime import date

import pytest

from app.data.participant_diagnostics import analyze_source
from tests.unit.test_nse_reference import VOL_2015


def test_preserves_fraction_and_reports_source_differences() -> None:
    text = VOL_2015.replace("290814,253340", "290814.3333,253340", 1)
    result = analyze_source(text.encode(), date(2015, 1, 5), "volume")
    assert result["date_status"] == "verified"
    assert result["has_fractional_values"] is True
    assert result["rows"]["Client"]["fut_idx_long"] == "290814.3333"
    assert result["source_total_differences"]["Client.long"] == "-0.3333"
    assert result["source_total_differences"]["TOTAL.fut_idx_long"] == "-0.3333"


def test_missing_date_never_becomes_verified_and_bad_numbers_fail() -> None:
    source = VOL_2015.replace(", 2015", "", 1)
    assert (
        analyze_source(source.encode(), date(2015, 1, 5), "volume")["date_status"] == "unverified"
    )
    with pytest.raises(ValueError, match="Invalid decimal"):
        analyze_source(VOL_2015.replace("290814", "NaN", 1).encode(), date(2015, 1, 5), "volume")
