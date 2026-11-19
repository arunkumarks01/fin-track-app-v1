from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.money import paisa_to_rupees, rupees_to_paisa


@pytest.mark.parametrize(
    ("rupees", "paisa"),
    [
        ("0", 0),
        ("1", 100),
        ("1234.5", 123_450),
        ("1234.56", 123_456),
        (Decimal("0.01"), 1),
        (2500, 250_000),
        ("-12.34", -1234),
        ("10000000", 1_000_000_000),  # ₹1 cr paper capital
    ],
)
def test_exact_conversion(rupees: Decimal | str | int, paisa: int) -> None:
    assert rupees_to_paisa(rupees) == paisa


@pytest.mark.parametrize("bad", [1.5, 0.1, True])
def test_floats_and_bools_rejected(bad: object) -> None:
    with pytest.raises(TypeError):
        rupees_to_paisa(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["0.001", "12.345", Decimal("1.005")])
def test_sub_paisa_rejected_not_rounded(bad: str | Decimal) -> None:
    with pytest.raises(ValueError, match="sub-paisa"):
        rupees_to_paisa(bad)


@pytest.mark.parametrize("bad", ["abc", "NaN", "Infinity", ""])
def test_garbage_rejected(bad: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 — message varies by failure
        rupees_to_paisa(bad)


def test_paisa_to_rupees_rejects_non_int() -> None:
    with pytest.raises(TypeError):
        paisa_to_rupees(1.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        paisa_to_rupees(True)


@given(st.integers(min_value=-(10**15), max_value=10**15))
def test_round_trip_is_lossless(paisa: int) -> None:
    rupees = paisa_to_rupees(paisa)
    assert isinstance(rupees, Decimal)
    assert rupees_to_paisa(rupees) == paisa
    assert rupees_to_paisa(str(rupees)) == paisa
