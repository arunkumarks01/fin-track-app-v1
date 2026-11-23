type Json = Record<string, unknown>;

function record(value: unknown): Json {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Json;
  throw new Error("Invalid trade plan");
}

function text(value: unknown): string {
  if (typeof value === "string") return value;
  throw new Error("Invalid trade plan text");
}

function integer(value: unknown): number {
  if (typeof value === "number" && Number.isSafeInteger(value)) return value;
  throw new Error("Invalid trade plan amount");
}

function optionalInteger(value: unknown): number | null {
  return value === null ? null : integer(value);
}

function texts(value: unknown): string[] {
  if (!Array.isArray(value)) throw new Error("Invalid risk checks");
  return value.map(text);
}

export function parseTradePlan(value: unknown) {
  if (value === null || value === undefined) return null;
  const obj = record(value);
  if (obj.status === "unavailable") {
    return { status: "unavailable" as const, reason: text(obj.reason) };
  }
  if (obj.status !== "draft" && obj.status !== "expired")
    throw new Error("Invalid trade plan status");
  const levels = record(obj.levels);
  const sizing = record(obj.sizing);
  const guardrails = record(obj.guardrails);
  if (levels.side !== "long" && levels.side !== "short") throw new Error("Invalid trade side");
  if (guardrails.mode !== "learning" && guardrails.mode !== "realistic")
    throw new Error("Invalid paper mode");
  if (sizing.mode !== "fixed_risk" && sizing.mode !== "fixed_lots" && sizing.mode !== "kelly")
    throw new Error("Invalid sizing mode");
  if (levels.target_basis !== "structure" && levels.target_basis !== "atr_extension")
    throw new Error("Invalid target basis");
  const expiresAt = obj.expires_at === null ? null : text(obj.expires_at);
  if (expiresAt !== null && !Number.isFinite(Date.parse(expiresAt)))
    throw new Error("Invalid trade plan expiry");
  return {
    status: obj.status as "draft" | "expired",
    side: levels.side,
    trigger: integer(levels.trigger_paisa),
    limit: integer(levels.entry_limit_paisa),
    stop: integer(levels.stop_paisa),
    target: integer(levels.target_paisa),
    support: integer(levels.support_paisa),
    atr: integer(levels.atr_paisa),
    rewardRiskBps: integer(levels.reward_risk_bps),
    riskPerUnit: integer(levels.risk_per_unit_paisa),
    targetBasis: levels.target_basis,
    trailingActivation: integer(levels.trailing_activation_paisa),
    trailingDistance: integer(levels.trailing_distance_paisa),
    lotSize: integer(obj.lot_size),
    sizingMode: sizing.mode,
    lots: optionalInteger(sizing.lots),
    units: optionalInteger(sizing.units),
    risk: optionalInteger(sizing.risk_paisa),
    notional: optionalInteger(sizing.notional_paisa),
    sizingNote: sizing.note === null ? null : text(sizing.note),
    riskBudget: integer(sizing.risk_budget_paisa),
    notionalCap: integer(sizing.notional_cap_paisa),
    capital: integer(obj.capital_paisa),
    paperMode: guardrails.mode,
    blocks: guardrails.blocks === true,
    warnings: texts(guardrails.warnings),
    unknown: texts(guardrails.unknown),
    entryWindow: text(obj.entry_window),
    expiresAt,
    priceBasis: text(obj.price_basis),
  };
}

export type TradePlan = NonNullable<ReturnType<typeof parseTradePlan>>;
