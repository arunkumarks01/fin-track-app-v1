"""Live marks: update position mark prices and calculate MTM."""

from __future__ import annotations


def calculate_mtm(
    mark_paisa: int,
    cost_basis_paisa_per_share: int,
    quantity: int,
) -> int:
    """
    Calculate mark-to-market P&L for an open position.

    Args:
        mark_paisa: Current price per share (paisa).
        cost_basis_paisa_per_share: Average entry price per share (paisa).
        quantity: Quantity held.

    Returns:
        Unrealized P&L in paisa (can be negative).
    """
    per_share_pnl = mark_paisa - cost_basis_paisa_per_share
    total_mtm = per_share_pnl * quantity
    return total_mtm


def portfolio_mtm(positions: dict[str, int]) -> int:
    """
    Sum unrealized P&L across all open positions.

    Args:
        positions: Dict mapping symbol → unrealized P&L (paisa).

    Returns:
        Total portfolio MTM (paisa).
    """
    return sum(positions.values())


def stale_mark_age_seconds(
    marked_at_unix: float,
    now_unix: float,
) -> float:
    """
    Calculate how old a mark is.

    Args:
        marked_at_unix: Timestamp when mark was last updated (seconds).
        now_unix: Current time (seconds).

    Returns:
        Age in seconds. If negative or very large (>1 day), mark is stale.
    """
    age = now_unix - marked_at_unix
    return age
