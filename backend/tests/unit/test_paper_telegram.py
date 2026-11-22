"""Tests for Telegram approval flow."""

from datetime import datetime, timedelta, timezone

import pytest

from app.broker.telegram import ApprovalRequest, compute_approval_ttl_seconds


class TestApprovalRequest:
    """Telegram approval request lifecycle."""

    @pytest.fixture
    def approval(self) -> ApprovalRequest:
        """Sample approval request."""
        now = datetime(2026, 11, 22, 13, 0, 0, tzinfo=timezone.utc)
        return ApprovalRequest(
            order_id=1,
            symbol="NSE:RELIANCE",
            side="buy",
            quantity=10,
            entry_price_paisa=200000,  # ₹2000
            stop_loss_paisa=190000,  # ₹1900
            target_paisa=220000,  # ₹2200
            sent_at=now,
            timeout_seconds=300,  # 5 min
        )

    def test_message_format(self, approval: ApprovalRequest) -> None:
        """Message includes symbol, qty, prices."""
        msg = approval.message_text()
        assert "BUY" in msg
        assert "10" in msg
        assert "RELIANCE" in msg
        assert "2000" in msg
        assert "1900" in msg or "SL" in msg

    def test_initial_status_pending(self, approval: ApprovalRequest) -> None:
        """New approval starts as pending."""
        assert approval.status == "pending"
        assert approval.responded_at is None

    def test_not_expired_within_timeout(self, approval: ApprovalRequest) -> None:
        """Still within 5 min → not expired."""
        now = approval.sent_at + timedelta(seconds=100)
        assert approval.is_expired(now) is False

    def test_expired_after_timeout(self, approval: ApprovalRequest) -> None:
        """After 5 min → expired."""
        now = approval.sent_at + timedelta(seconds=301)
        assert approval.is_expired(now) is True

    def test_approve_updates_status(self, approval: ApprovalRequest) -> None:
        """Approving sets status and timestamp."""
        now = approval.sent_at + timedelta(seconds=10)
        approval.approve(now)
        assert approval.status == "approved"
        assert approval.responded_at == now

    def test_reject_updates_status(self, approval: ApprovalRequest) -> None:
        """Rejecting sets status and reason."""
        now = approval.sent_at + timedelta(seconds=10)
        approval.reject(now, "Quantity too high")
        assert approval.status == "rejected"
        assert approval.responded_at == now
        assert "Quantity" in approval.response_text

    def test_mark_timed_out(self, approval: ApprovalRequest) -> None:
        """Mark as timed out."""
        now = approval.sent_at + timedelta(seconds=301)
        approval.mark_timed_out(now)
        assert approval.status == "timed_out"
        assert "timeout" in approval.response_text.lower()


class TestApprovalTTL:
    """Time-to-live calculation."""

    def test_ttl_within_window(self) -> None:
        """50 seconds elapsed of 300 → 250 remaining."""
        sent = datetime(2026, 11, 22, 13, 0, 0, tzinfo=timezone.utc)
        now = sent + timedelta(seconds=50)
        ttl = compute_approval_ttl_seconds(sent, now, timeout_seconds=300)
        assert ttl == 250

    def test_ttl_at_expiry(self) -> None:
        """300 seconds elapsed → 0 remaining."""
        sent = datetime(2026, 11, 22, 13, 0, 0, tzinfo=timezone.utc)
        now = sent + timedelta(seconds=300)
        ttl = compute_approval_ttl_seconds(sent, now, timeout_seconds=300)
        assert ttl == 0

    def test_ttl_past_expiry_clamps_to_zero(self) -> None:
        """301 seconds elapsed → 0 (not negative)."""
        sent = datetime(2026, 11, 22, 13, 0, 0, tzinfo=timezone.utc)
        now = sent + timedelta(seconds=301)
        ttl = compute_approval_ttl_seconds(sent, now, timeout_seconds=300)
        assert ttl == 0
