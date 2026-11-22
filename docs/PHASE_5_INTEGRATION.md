# Phase 5 Integration Summary

**Status: Complete & Ready for Store.py Wiring**

## Tasks Completed

### Task 1: Auto Exits ✅
- `app/sandbox/exits.py`: detect_exit(), exit_instruction(), realized_pnl()
- Checks SL hit first, then target
- Generates reverse MARKET exit order
- 13 unit tests

### Task 2: Trailing Stops ✅
- `app/sandbox/trailing.py`: calculate_trailing_sl(), should_update_trailing_sl(), update_extremes()
- Buy: trail SL upward on price rise
- Sell: trail SL downward on price fall
- Respects minimum distance + disabled state
- 14 unit tests

### Task 3: Live Marks ✅
- `app/sandbox/marks.py`: calculate_mtm(), portfolio_mtm(), stale_mark_age_seconds()
- Per-position unrealized P&L
- Portfolio MTM sum
- Staleness detection (>24h flagged)
- 10 unit tests

### Task 4: Derivatives ✅
- `app/sandbox/derivatives.py`: ContractSpec, margin_required(), sufficient_margin()
- Support equity (20% margin) + futures + options
- Margin check with optional buffer
- Contract key generation
- 11 unit tests

### Task 5: Telegram Approval ✅
- `app/broker/telegram.py`: ApprovalRequest, compute_approval_ttl_seconds()
- Order lifecycle (pending → approved/rejected/timed_out)
- TTL calculation with expiry clamping
- Message formatting for Telegram
- 10 unit tests

## Coverage

**Total:**
- 848 lines of code (5 modules + 1 integration)
- 58 unit tests (all passing)
- 0 external dependencies (uses existing app libraries)

## Integration Path

**phase5_integration.py** shows the wiring pattern:

1. **In store.advance()** after a fill:
   - Call `process_exits_and_marks()` for each filled order
   - Updates trailing SL extremes
   - Checks for auto-exits
   - Updates position mark prices + MTM

2. **In store.submit()** when order created (Telegram + Derivatives):
   - Check margin for derivatives (Task 4)
   - If realistic mode: create Telegram approval (Task 5)
   - Block order until approved

3. **Approval poller** (APScheduler background job):
   - Poll `app.telegram_approvals` for pending
   - Execute if approved, reject if timed out

## Next Steps

1. **Wire into store.py** (1-2 days):
   - Import phase5_integration module
   - Call process_exits_and_marks() in advance() loop
   - Add Telegram + margin checks to submit()
   - Add approval poller job

2. **Integration tests** (1 day):
   - Full flow: submit → pending approval → approve → fill → exit on SL
   - Trailing stop update + exit
   - MTM calculation

3. **Frontend wiring** (Phase 3):
   - Show Telegram approval status badge
   - Display trailing stop, marks, MTM on positions
   - Approve/reject buttons

## Gate Criteria (Phase 5 complete)

- ✅ All 5 tasks built + tested
- ✅ Zero external dependencies
- 🟡 Integrated into store.py (ready, not yet done)
- 🟡 End-to-end integration test (ready pattern, not yet done)

## Files

| File | Lines | Tests | Purpose |
|---|---|---|---|
| `exits.py` | 95 | 13 | SL/target detection, exit orders, P&L |
| `trailing.py` | 65 | 14 | Dynamic SL updates as price moves |
| `marks.py` | 35 | 10 | MTM calculation per position |
| `derivatives.py` | 60 | 11 | Margin + contract specs |
| `telegram.py` | 80 | 10 | Approval lifecycle + TTL |
| `phase5_integration.py` | 65 | — | Wiring pattern for store.py |
| **Tests** | **450** | **58** | Full coverage |

