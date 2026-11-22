# Phase 5 — Paper sandbox + safety (integration sequence)

> Status: Starting. Phase 4 (planning + sizing) complete; Phase 5 (execution integration) next.
> Build order: **auto-exits → trailing stops → live marks → derivatives → Telegram approval.**

---

## 1. Auto exits (foundation for all exits)

**What it does:** When a filled order has a risk stop / risk target, detect when price hits those levels and place an automatic exit order.

**Implementation:**

### Backend (`app/sandbox/exits.py` — new file)
- **Exit detector** function: takes order + fills, bar, detect if SL or target hit
  - Buy order SL: `bar.low <= risk_stop_paisa`
  - Buy order target: `bar.high >= risk_target_paisa`
  - Sell order SL: `bar.high >= risk_stop_paisa`
  - Sell order target: `bar.low <= risk_target_paisa`
  - Return `ExitTrigger` with which level (sl/target) + exit price (worst of bar open/level)
- **Exit order factory:** converts trigger → sell Instruction (inverse side, quantity = filled, same symbol)

### Database change
- `app.paper_orders`: add columns:
  - `exit_order_id bigint` (self-referential, the generated exit order if auto-triggered)
  - `exit_reason text` ('target_hit' | 'stop_hit' | 'manual')
  - `exit_triggered_at timestamptz` (when detector fired)

### Store logic (`app/sandbox/store.py` — `advance()` extension)
- When stepping to a new day:
  1. Load all filled orders for the account that have no exit yet
  2. For each, fetch the day's bar
  3. Run exit detector
  4. If triggered, create exit order (auto-submit as MARKET) + link it
  5. Mark realized P&L (cost basis of filled qty × exit price − buy price)

### Tests (`tests/unit/test_paper_exits.py`)
- Detector: buy SL hit, buy target hit, sell SL hit, sell target hit, no hit
- Exit order creation + idempotency (same order never exits twice)
- P&L calculation: buy then exit at profit/loss, sell then exit
- Partial fill edge case: 100 qty filled, 50 exit, then 50 more exit

### API extension (`app/api/paper.py`)
- No new routes; exits are automatic on `POST /api/paper/accounts/{id}/advance`

---

## 2. Trailing stops (depends on auto-exits)

**What it does:** After a filled order, dynamically update the SL if price moves in the profit direction.

**How it works:**
- Trailing stop rule: "move SL up by X when price moves Y" (config in `risk_parameters`)
- Example: buy 100 @ ₹2000, SL ₹1980; if price hits ₹2050, move SL to ₹2030 (trailing by ₹20)

**Implementation:**

### Model (`app/sandbox/model.py` — extend `Instruction`)
- Add `trailing_stop_bps` field (0 = disabled, 500 = 5% trailing)
- Add `trailing_stop_paisa_min` (minimum move to trigger an update)

### Database change
- `app.paper_orders`: add columns:
  - `trailing_stop_bps smallint` (0 = disabled)
  - `trailing_stop_paisa_min int` (e.g. 50 = ₹50)
  - `highest_paisa_seen int` (for buy: highest price hit; for sell: lowest price hit)
  - `current_stop_paisa int` (live SL, may drift up/down)

### Store logic (`app/sandbox/store.py` — `advance()` extension after auto-exits)
- For each filled order with trailing enabled:
  1. Get highest/lowest price hit so far
  2. Calculate new SL based on trail config
  3. If new SL is higher (buy) / lower (sell) than current SL, update `current_stop_paisa`
  4. Use `current_stop_paisa` in exit detector (not the original `risk_stop_paisa`)

### Tests (`tests/unit/test_paper_trailing_stops.py`)
- Trailing enabled, price moves profit direction, SL moves, new bar closes below moved SL → exits
- Trailing enabled, price reverses → SL doesn't move back
- Trailing disabled → SL stays original
- Partial fill edge case

---

## 3. Live marks (supports P&L updates on fills)

**What it does:** When a new quote arrives (via ticks or snapshot), update open positions' mark prices and show MTM P&L.

**Implementation:**

### Database change
- `app.paper_positions`: already has `mark_paisa + mark_day`; add:
  - `mark_updated_at timestamptz`
  - `mtm_paisa bigint` (cached MTM for display, not live computed)

### New table `app.paper_marks` (optional, for mark history)
- Columns: `account_id, symbol, mark_paisa, marked_at timestamptz`
- Purpose: audit trail of mark updates (useful for debugging slippage)

### Store logic (`app/sandbox/marks.py` — new file)
- **Update marks** function: takes account_id, quotes dict (symbol → price), updates all open positions
  - Query `app.paper_positions WHERE account_id AND quantity > 0`
  - For each position, fetch new quote, update `mark_paisa` + `mark_updated_at`
  - Calculate MTM: `(mark_paisa − cost_basis_paisa_per_share) × quantity`
  - Sum across positions for portfolio MTM

### Integration points
- Called by manual UI refresh (GET mark)
- Called by advance() when stepping (fetch EOD prices from daily bars)
- Called by intraday scanner (fetch mid-day quotes)
- **Not** called on every tick (would be too chatty; aggregated per update)

### API extension (`app/api/paper.py`)
- `GET /api/paper/accounts/{id}/marks` → returns current marks + portfolio MTM
- `POST /api/paper/accounts/{id}/marks` (admin) → manual mark update from live quotes

### Tests (`tests/unit/test_paper_marks.py`)
- Mark update on a position, MTM calculated correctly
- Multiple positions, portfolio MTM sums
- Stale mark flagged (mark age > threshold)

---

## 4. Derivatives (futures + options ordering)

**What it does:** Accept futures and options orders, store contract specs, check margin.

**Scope for Phase 5:**
- Accept futures & options symbols (NSE:NIFTY1DEC26, NSE:NIFTY1DEC26C26000)
- Fetch contract specs (multiplier, tick size, margin req)
- Block order if insufficient margin (realistic mode)
- Store order + fills same as equities, but show contract detail

**What's NOT in Phase 5:**
- Greeks (Phase 2 analytics)
- Multi-leg orders (Phase 7)
- Assignment/expiry settlement (Phase 6+)

**Implementation:**

### Database changes
- `app.paper_orders`: add columns:
  - `contract_type text` ('equity' | 'fut' | 'opt_ce' | 'opt_pe')
  - `expiry_date date` (futures/options expiry, NULL for equity)
  - `strike_paisa int` (options only)
  - `multiplier int` (futures multiplier, 1 for equity)

- New table `app.derivatives_specs` (from Kite + NSE)
  - `symbol, contract_type, expiry, strike, multiplier, tick_size, margin_initial, margin_maint`

### Store logic (`app/sandbox/derivatives.py` — new file)
- **Fetch spec** function: symbol → contract_type/expiry/strike/multiplier/margin
  - Calls Kite (or mocked)
  - Caches in app cache for session
- **Margin check** function: account_id + order → margin required, available margin
  - Sum of all open positions' margin requirement
  - New order's margin req
  - Block if total exceeds available (realistic mode only)

### Order submission extended (`store.submit()`)
- If symbol has derivatives prefix (NSE:NIFTY1DEC26):
  1. Fetch spec
  2. If realistic mode, check margin
  3. Store with contract_type/expiry/strike/multiplier
  4. Same fill/exit flow

### Tests (`tests/unit/test_paper_derivatives.py`)
- Fetch futures contract spec, order placed, margin checked
- Options order placed with strike
- Insufficient margin in realistic mode → rejected
- Learning mode allows over-margin (warning only)

---

## 5. Telegram approval (semi-auto flow)

**What it does:** When a new candidate is proposed (Phase 3 scorer) or manual order submitted, send Telegram approval request; user approves/rejects/delays via inline buttons.

**Scope for Phase 5:**
- Paper orders (learning mode): approval *optional*, informational only
- Paper orders (realistic mode): approval *required* before order placed

**What's NOT in Phase 5:**
- Live order approval (Phase 11)
- Partial approvals / quantity negotiation
- Approval scheduling (later enhancement)

**Implementation:**

### Environment + config
- `TELEGRAM_BOT_TOKEN` (new, required if Telegram enabled)
- `TELEGRAM_CHAT_ID` (target chat)
- `TELEGRAM_TIMEOUT_SECONDS` (default 300 = 5 min; order auto-rejects if no response)

### Database changes
- New table `app.telegram_approvals`
  - `id, account_id, order_id, message_id, status ('pending'|'approved'|'rejected'|'timed_out')`
  - `sent_at, responded_at, response_text`

### Backend (`app/broker/telegram.py` — new file)
- **Send approval** function: order details → formatted message + buttons
  - Message: "New order: BUY 50 RELIANCE @ market, SL ₹2680, target ₹2750, R:R 1.4"
  - Buttons: ✅ Approve | ❌ Reject | ⏱️ Later (5 min)
  - Stores message_id for polling
- **Poll response** function: message_id → user's choice (via Telegram Bot API get_message)
- **Execute if approved** or **reject if timeout**

### Store logic (`app/sandbox/store.py` — `submit()` modified)
- If Telegram enabled + realistic mode:
  1. Create order with status = 'pending_approval'
  2. Send Telegram approval request
  3. Store approval record
  4. Return 202 (accepted but not yet filled)
- Background job checks approvals every 10s:
  - If approved, set order status = 'pending', proceed with normal fill logic
  - If rejected/timed out, set status = 'rejected', log reason

### APScheduler job (`app/core/scheduler.py` register)
- Job: `poll_telegram_approvals`, runs every 10s
  - Query `app.telegram_approvals WHERE status = 'pending'`
  - For each, poll Telegram for response
  - Update status + order status accordingly
  - Proceed with fill if approved

### API extension (`app/api/paper.py`)
- `POST /api/paper/accounts/{id}/orders` → returns 202 + approval_url if pending Telegram
- Webhook endpoint (private, bearer-protected) for Telegram button callbacks (optional, can poll instead)

### Tests (`tests/integration/test_paper_telegram.py`)
- Send approval, mock Telegram response approve → order filled
- Send approval, timeout → order rejected
- Reject button → order rejected immediately
- Learning mode: approval sent but order placed regardless (async notification)

---

## Implementation order (by dependency)

| # | Task | Depends on | Est. time | Status |
|---|---|---|---|---|
| 1 | Auto exits | Phase 4 (done) | 2-3 days | Next |
| 2 | Trailing stops | Auto exits | 1-2 days | After 1 |
| 3 | Live marks | Auto exits | 1-2 days | Parallel to 2 |
| 4 | Derivatives | Phase 4 (specs exist) | 2-3 days | After 1 |
| 5 | Telegram approval | (independent) | 2-3 days | Parallel to 3-4 |
| **Phase 5 total** | | | **~10 days** | |

**Can start in parallel:** 2+3, 4, 5 (after 1 is merged).
**Gate before Phase 6:** auto-exits + live marks + derivatives working end-to-end, Telegram approved orders tested.

---

## Frontend changes (Phase 5)

Already scaffolded in `/app/paper/`:
- Order form → auto-populated with SL/target from candidate
- Trailing stop checkbox + % field
- Position list → shows current mark, MTM, unrealized P&L
- Telegram approval status (pending/approved/rejected badge)

No new components needed; wire existing ones to Phase 5 backend.

---

## Testing checkpoint (Phase 5 gate)

Before Phase 6 starts:
- ✅ Exit detector: 100% of exits at correct price
- ✅ Trailing stops: price drift within tolerance, SL updates logged
- ✅ Live marks: MTM within 1 paisa of hand-computed
- ✅ Derivatives: margin checks enforced, specifier accurate
- ✅ Telegram: approve/reject/timeout all paths tested
- ✅ Parity: paper orders match backtest signals (same entry, same SL/target prices)

