type Data = Record<string, unknown>;

function record(value: unknown): Data {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Data;
  throw new Error("Invalid paper trading response.");
}
function rows(value: unknown): Data[] {
  if (!Array.isArray(value)) throw new Error("Invalid paper trading table.");
  return value.map(record);
}
function integer(value: unknown): number {
  if (typeof value === "number" && Number.isSafeInteger(value)) return value;
  throw new Error("Invalid paper trading amount.");
}
function text(value: unknown): string {
  if (typeof value === "string") return value;
  throw new Error("Invalid paper trading text.");
}
function optional(value: unknown): string | null {
  return value == null ? null : text(value);
}

export type PaperMode = "learning" | "realistic";
export type PaperKind = "market" | "limit" | "stop_market" | "gtt";
export type PaperSide = "buy" | "sell";

function mode(value: unknown): PaperMode {
  if (value === "learning" || value === "realistic") return value;
  throw new Error("Invalid paper trading mode.");
}

function account(value: unknown) {
  const row = record(value);
  return {
    id: integer(row.id),
    name: text(row.name),
    mode: mode(row.paper_mode),
    start: text(row.start_day),
    cursor: optional(row.cursor_day),
    cash: integer(row.cash_paisa),
    realized: integer(row.realized_paisa),
    capital: integer(row.capital_paisa),
  };
}

async function request(path: string, method = "GET", payload?: unknown, token?: string) {
  const res = await fetch(`/api/paper${path}`, {
    method,
    cache: "no-store",
    redirect: "error",
    ...(payload !== undefined ? { body: JSON.stringify(payload) } : {}),
    headers: token ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } : {},
  });
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : undefined;
    if (res.status === 404 && detail === "Not Found")
      throw new Error("The running backend does not yet serve paper trading.");
    if (res.status === 401) throw new Error("Admin API token rejected.");
    if (Array.isArray(detail)) {
      const fields = detail.map((e) => optional(record(e).msg)).filter(Boolean);
      throw new Error(fields.join(" · ") || "Check the form values.");
    }
    throw new Error(typeof detail === "string" ? detail : `Paper request failed (${res.status}).`);
  }
  return record(body);
}

export async function paperAccounts() {
  const body = await request("/accounts");
  const coverage = record(body.coverage);
  const controls = record(body.controls);
  return {
    accounts: rows(body.accounts).map(account),
    oldest: optional(coverage.oldest),
    newest: optional(coverage.newest),
    paused: controls.paused === true,
    killed: controls.killed === true,
    defaultMode: mode(body.default_mode),
  };
}

export async function paperOverview(id: number) {
  const body = await request(`/accounts/${id}`);
  const model = record(body.model);
  return {
    account: account(body.account),
    equity: integer(body.equity_paisa),
    unrealized: integer(body.unrealized_paisa),
    marketValue: integer(body.market_value_paisa),
    slippageBps: integer(model.slippage_bps),
    volumeBps: integer(model.volume_bps),
    positions: rows(body.positions).map((p) => ({
      symbol: text(p.symbol),
      quantity: integer(p.quantity),
      cost: integer(p.cost_paisa),
      mark: integer(p.mark_paisa),
      day: text(p.mark_day),
    })),
    orders: rows(body.orders).map((o) => ({
      id: integer(o.id),
      symbol: text(o.symbol),
      side: text(o.side),
      kind: text(o.kind),
      status: text(o.status),
      quantity: integer(o.quantity),
      filled: integer(o.filled),
      detail: optional(o.detail),
    })),
    fills: rows(body.fills).map((f) => ({
      id: integer(f.id),
      symbol: text(f.symbol),
      day: text(f.trade_day),
      side: text(f.side),
      quantity: integer(f.quantity),
      price: integer(f.price_paisa),
      fees: integer(f.fees_paisa),
      charges: Object.entries(record(f.fee_breakdown)).map(([name, amount]) => ({
        name,
        amount: integer(amount),
      })),
      realized: f.realized_paisa === null ? null : integer(f.realized_paisa),
    })),
  };
}

export async function createPaper(name: string, start: string, mode: PaperMode, token: string) {
  return account(await request("/accounts", "POST", { name, start_day: start, mode }, token));
}

export function paperPrice(input: string): number | null {
  if (!input.trim()) return null;
  const matched = /^(\d{1,8})(?:\.(\d{1,2}))?$/.exec(input.trim());
  if (!matched) throw new Error("Enter a rupee price with up to two decimal places.");
  const amount = Number(matched[1]) * 100 + Number((matched[2] ?? "").padEnd(2, "0"));
  if (amount <= 0) throw new Error("Price must be above zero.");
  return amount;
}

export function queuePaper(
  id: number,
  requestId: string,
  instruction: {
    symbol: string;
    side: PaperSide;
    kind: PaperKind;
    quantity: number;
    limit_paisa: number | null;
    trigger_paisa: number | null;
    risk_stop_paisa: number | null;
    risk_target_paisa: number | null;
  },
  token: string,
) {
  return request(`/accounts/${id}/orders`, "POST", { request_id: requestId, instruction }, token);
}

export function stepPaper(id: number, day: string | null, token: string) {
  return request(`/accounts/${id}/advance`, "POST", { expected_day: day }, token);
}

export function cancelPaper(id: number, order: number, token: string) {
  return request(`/accounts/${id}/orders/${order}/cancel`, "POST", {}, token);
}

export function paperControls(change: { paused?: boolean; killed?: boolean }, token: string) {
  return request("/controls", "PUT", change, token);
}

export type PaperOverview = Awaited<ReturnType<typeof paperOverview>>;
