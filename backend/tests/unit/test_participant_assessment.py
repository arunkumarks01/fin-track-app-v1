import pytest

from app.data.participant_assessment import assess_source


def test_uses_existing_tolerance_and_keeps_exact_source_differences() -> None:
    differences = {"Client.long": "-0.3333", "TOTAL.fut_idx_long": "0.0001"}
    result = assess_source("verified", differences, 2)
    assert result["outcome"] == "reconciled_within_tolerance"
    assert result["source_differences"] == differences


def test_material_printed_total_diff_is_separate_from_component_sum() -> None:
    assert (
        assess_source("verified", {"Client.short": "2782489"}, 2)["outcome"]
        == "component_totals_disagree"
    )
    assert (
        assess_source("verified", {"TOTAL.fut_idx_long": "3"}, 2)["outcome"]
        == "component_sums_disagree"
    )
    assert (
        assess_source("verified", {"TOTAL.long": "500000"}, 2)["outcome"]
        == "component_totals_disagree"
    )


def test_unverified_date_fails_closed_even_if_totals_reconcile() -> None:
    assert assess_source("unverified", {}, 2)["outcome"] == "source_date_unverified"
    with pytest.raises(ValueError, match="Invalid"):
        assess_source("verified", {"Client.long": "NaN"}, 2)
