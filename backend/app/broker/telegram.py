"""Telegram approval flow for paper trading orders."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

ApprovalStatus = Literal["pending", "approved", "rejected", "timed_out"]


class ApprovalRequest:
    """An order awaiting user approval via Telegram."""

    def __init__(
        self,
        order_id: int,
        symbol: str,
        side: str,
        quantity: int,
        entry_price_paisa: int,
        stop_loss_paisa: int | None,
        target_paisa: int | None,
        sent_at: datetime,
        timeout_seconds: int = 300,
    ) -> None:
        self.order_id = order_id
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.entry_price_paisa = entry_price_paisa
        self.stop_loss_paisa = stop_loss_paisa
        self.target_paisa = target_paisa
        self.sent_at = sent_at
        self.timeout_seconds = timeout_seconds
        self.status: ApprovalStatus = "pending"
        self.responded_at: datetime | None = None
        self.response_text: str | None = None

    def message_text(self) -> str:
        """Format order details for Telegram message."""
        entry_rupees = self.entry_price_paisa / 100
        sl_rupees = self.stop_loss_paisa / 100 if self.stop_loss_paisa else "—"
        target_rupees = self.target_paisa / 100 if self.target_paisa else "—"

        return (
            f"🔔 {self.side.upper()} {self.quantity} {self.symbol}\n"
            f"Entry: ₹{entry_rupees:.2f}\n"
            f"SL: {sl_rupees}\n"
            f"Target: {target_rupees}\n"
            f"⏱️ Approve within 5 min"
        )

    def is_expired(self, now: datetime) -> bool:
        """Check if approval request has timed out."""
        elapsed = (now - self.sent_at).total_seconds()
        return elapsed > self.timeout_seconds

    def approve(self, now: datetime) -> None:
        """Mark as approved."""
        self.status = "approved"
        self.responded_at = now

    def reject(self, now: datetime, reason: str = "User rejected") -> None:
        """Mark as rejected."""
        self.status = "rejected"
        self.responded_at = now
        self.response_text = reason

    def mark_timed_out(self, now: datetime) -> None:
        """Mark as timed out."""
        self.status = "timed_out"
        self.responded_at = now
        self.response_text = "No response within timeout window"


def compute_approval_ttl_seconds(
    sent_at: datetime,
    now: datetime,
    timeout_seconds: int = 300,
) -> int:
    """
    Time remaining before approval expires.

    Returns:
        Seconds left (can be 0 or negative if already expired).
    """
    elapsed = (now - sent_at).total_seconds()
    remaining = timeout_seconds - elapsed
    return max(0, int(remaining))
