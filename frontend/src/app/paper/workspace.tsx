"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { fetchSymbols } from "@/lib/charts";
import { formatInt, formatPct, formatRupees } from "@/lib/format";
import {
  cancelPaper,
  createPaper,
  paperAccounts,
  paperControls,
  paperOverview,
  paperPrice,
  queuePaper,
  stepPaper,
  type PaperKind,
  type PaperMode,
  type PaperOverview,
  type PaperSide,
} from "@/lib/paper";

const field = "h-9 w-full rounded-md border bg-background px-2.5 text-sm";
const label = "grid gap-1 text-xs font-medium";
const PANEL = "rounded-xl border bg-card p-4";
const KINDS: Record<PaperKind, string> = {
  market: "Market",
  limit: "Limit",
  stop_market: "Stop market",
  gtt: "GTT",
};

export function PaperWorkspace({ symbol }: { symbol?: string }) {
  const client = useQueryClient();
  const listing = useQuery({ queryKey: ["paper-accounts"], queryFn: paperAccounts, retry: false });
  const [selected, select] = useState<number | null>(null);
  const [token, setToken] = useState("");
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null);
  const accountId = selected ?? listing.data?.accounts[0]?.id ?? null;
  const accountQuery = useQuery({
    queryKey: ["paper-overview", accountId],
    enabled: accountId !== null,
    queryFn: () => paperOverview(accountId!),
    retry: false,
  });
  const account = accountQuery.data;
  const [showNew, setShowNew] = useState(false);
  const [name, setName] = useState("Paper practice");
  const [start, setStart] = useState("");
  const [mode, setMode] = useState<PaperMode | null>(null);
  const showCreate = showNew || listing.data?.accounts.length === 0;
  const blocked = listing.data?.paused || listing.data?.killed;
  const locked = pending || !token.trim();

  async function act(action: () => Promise<unknown>, success: string) {
    if (locked) return false;
    setPending(true);
    setNotice(null);
    try {
      await action();
      setNotice({ error: false, text: success });
      await Promise.all([
        client.invalidateQueries({ queryKey: ["paper-accounts"] }),
        client.invalidateQueries({ queryKey: ["paper-overview"] }),
      ]);
      return true;
    } catch (error) {
      setNotice({
        error: true,
        text: error instanceof Error ? error.message : "Paper action failed.",
      });
      return false;
    } finally {
      setPending(false);
    }
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const day = start || listing.data?.newest;
    if (!day) return;
    await act(async () => {
      const created = await createPaper(
        name,
        day,
        mode ?? listing.data?.defaultMode ?? "learning",
        token.trim(),
      );
      select(created.id);
      setShowNew(false);
    }, "Replay account created.");
  }

  if (listing.isPending) return <p role="status">Loading paper accounts…</p>;
  if (listing.error)
    return (
      <p role="alert" className="text-loss">
        {listing.error.message}
      </p>
    );
  const books = listing.data.accounts;
  return (
    <div className="space-y-4">
      <div className={`${PANEL} flex flex-wrap items-end gap-3`}>
        {books.length > 0 && (
          <label className={label}>
            Replay account
            <select
              className={field}
              value={accountId ?? ""}
              onChange={(e) => {
                select(Number(e.target.value));
                setNotice(null);
              }}
              disabled={pending}
            >
              {books.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {books.length > 0 && (
          <Button variant="outline" onClick={() => setShowNew(!showNew)} disabled={pending}>
            {showNew ? "Close new account" : "New account"}
          </Button>
        )}
        <label className={`${label} min-w-44 md:ml-auto`}>
          Admin API token
          <input
            className={field}
            type="password"
            autoComplete="off"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        </label>
        {token && (
          <Button variant="ghost" onClick={() => setToken("")} disabled={pending}>
            Clear token
          </Button>
        )}
      </div>

      {notice && (
        <p
          className={`text-sm ${notice.error ? "text-loss" : "text-muted-foreground"}`}
          role={notice.error ? "alert" : "status"}
        >
          {notice.text}
        </p>
      )}

      {showCreate && (
        <form onSubmit={create} className={`${PANEL} space-y-3`}>
          <h2 className="font-semibold">Start a replay</h2>
          <p className="text-sm text-muted-foreground">
            {listing.data.oldest
              ? `Finalized archive · ${listing.data.oldest} to ${listing.data.newest}`
              : "No verified NSE archive sessions are available."}
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className={label}>
              Name
              <input
                className={field}
                maxLength={60}
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </label>
            <label className={label}>
              Start date
              <input
                className={field}
                type="date"
                value={start || listing.data.newest || ""}
                min={listing.data.oldest ?? undefined}
                max={listing.data.newest ?? undefined}
                onChange={(e) => setStart(e.target.value)}
                required
              />
            </label>
            <label className={label}>
              Mode
              <select
                className={field}
                value={mode ?? listing.data.defaultMode}
                onChange={(e) => setMode(e.target.value as PaperMode)}
              >
                <option value="learning">Learning</option>
                <option value="realistic">Realistic</option>
              </select>
            </label>
            <Button type="submit" disabled={locked || !listing.data.newest}>
              Create account
            </Button>
          </div>
        </form>
      )}

      {accountQuery.error && (
        <p role="alert" className="text-sm text-loss">
          {accountQuery.error.message}
        </p>
      )}
      {accountQuery.isFetching && !account && accountId !== null && (
        <p role="status">Loading replay account…</p>
      )}
      {account && (
        <>
          <section className={`${PANEL} space-y-4`} aria-label="Paper portfolio">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold">{account.account.name}</h2>
                <p className="mt-0.5 text-sm text-muted-foreground">
                  {account.account.mode === "learning" ? "Learning" : "Realistic"} ·{" "}
                  {account.account.cursor
                    ? `After close of ${account.account.cursor}`
                    : `Before session ${account.account.start}`}
                  {blocked && " · Paper trading paused"}
                </p>
              </div>
              <Button
                disabled={locked || !!blocked}
                onClick={() =>
                  void act(
                    () => stepPaper(account.account.id, account.account.cursor, token.trim()),
                    "Replay advanced by one NSE session.",
                  )
                }
              >
                Advance one session
              </Button>
            </div>
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                ["Cash", account.account.cash],
                ["Equity", account.equity],
                ["Realized P&L", account.account.realized],
                ["Unrealized P&L", account.unrealized],
              ].map(([name, amount]) => (
                <div key={name} className="rounded-md bg-muted/50 p-3">
                  <dt className="text-xs text-muted-foreground">{name}</dt>
                  <dd className="mt-1 text-sm font-semibold tabular-nums">
                    {formatRupees(Number(amount))}
                  </dd>
                </div>
              ))}
            </dl>
          </section>

          <div className="grid items-start gap-4 lg:grid-cols-[minmax(16rem,23rem)_minmax(0,1fr)]">
            <OrderForm
              key={account.account.id}
              symbol={symbol}
              account={account}
              locked={locked || !!blocked}
              queue={(key, order) =>
                act(
                  () => queuePaper(account.account.id, key, order, token.trim()),
                  "Paper order queued. Advance one session to evaluate it.",
                )
              }
            />
            <div className="grid min-w-0 gap-4">
              <DataTable
                title="Positions"
                columns={["Symbol", "Shares", "Cost incl. fees", "Last mark", "Mark date"]}
                empty="No open positions"
              >
                {account.positions.map((p) => (
                  <tr key={p.symbol}>
                    <td>{p.symbol}</td>
                    <td>{formatInt(p.quantity)}</td>
                    <td>{formatRupees(p.cost)}</td>
                    <td>{formatRupees(p.mark)}</td>
                    <td>{p.day}</td>
                  </tr>
                ))}
              </DataTable>
              <DataTable
                title="Orders"
                columns={["Order", "Status", "Filled", "Action"]}
                empty="No orders queued"
              >
                {account.orders.map((o) => (
                  <tr key={o.id}>
                    <td>
                      {o.symbol}
                      <span className="block text-xs text-muted-foreground">
                        {o.side} · {KINDS[o.kind as PaperKind] ?? o.kind}
                      </span>
                    </td>
                    <td className="capitalize">
                      {o.status.replaceAll("_", " ")}
                      {o.detail && (
                        <span className="block max-w-60 text-xs normal-case text-muted-foreground">
                          {o.detail}
                        </span>
                      )}
                    </td>
                    <td>
                      {formatInt(o.filled)} / {formatInt(o.quantity)}
                    </td>
                    <td>
                      {(o.status === "pending" || o.status === "partially_filled") && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={locked}
                          onClick={() =>
                            void act(
                              () => cancelPaper(account.account.id, o.id, token.trim()),
                              "Order cancelled.",
                            )
                          }
                        >
                          Cancel
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </DataTable>
              <DataTable
                title="Fills"
                columns={["Session", "Symbol", "Shares", "Price", "Fees", "Realized P&L"]}
                empty="No simulated fills"
              >
                {account.fills.map((f) => (
                  <tr key={f.id}>
                    <td>{f.day}</td>
                    <td>
                      {f.symbol}
                      <span className="block text-xs text-muted-foreground">{f.side}</span>
                    </td>
                    <td>{formatInt(f.quantity)}</td>
                    <td>{formatRupees(f.price)}</td>
                    <td>
                      <details>
                        <summary className="cursor-pointer">{formatRupees(f.fees)}</summary>
                        <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                          {f.charges.map(({ name, amount }) => (
                            <li className="whitespace-nowrap capitalize" key={name}>
                              {name.replaceAll("_", " ")} · {formatRupees(amount)}
                            </li>
                          ))}
                        </ul>
                      </details>
                    </td>
                    <td>{f.realized === null ? "—" : formatRupees(f.realized)}</td>
                  </tr>
                ))}
              </DataTable>
            </div>
          </div>
        </>
      )}

      <details className={PANEL}>
        <summary className="cursor-pointer text-sm font-semibold">Paper controls</summary>
        <p className="my-2 text-sm text-muted-foreground">
          Pause and kill switch apply to all replay accounts. Pending orders remain on the ledger.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={locked}
            onClick={() =>
              void act(
                () => paperControls({ paused: !listing.data.paused }, token.trim()),
                listing.data.paused ? "Paper pause cleared." : "Paper trading paused.",
              )
            }
          >
            {listing.data.paused ? "Clear pause" : "Pause paper trading"}
          </Button>
          <Button
            variant="outline"
            disabled={locked}
            onClick={() =>
              void act(
                () => paperControls({ killed: !listing.data.killed }, token.trim()),
                listing.data.killed ? "Paper kill switch cleared." : "Paper kill switch enabled.",
              )
            }
          >
            {listing.data.killed ? "Clear kill switch" : "Enable kill switch"}
          </Button>
        </div>
      </details>
    </div>
  );
}

type Order = Parameters<typeof queuePaper>[2];
function OrderForm({
  symbol,
  account,
  locked,
  queue,
}: {
  symbol?: string;
  account: PaperOverview;
  locked: boolean;
  queue: (id: string, order: Order) => Promise<boolean>;
}) {
  const [input, setInput] = useState(symbol ?? "NSE:RELIANCE");
  const symbols = useQuery({ queryKey: ["symbols"], queryFn: fetchSymbols, staleTime: 300_000 });
  const [side, setSide] = useState<PaperSide>("buy");
  const [kind, setKind] = useState<PaperKind>("market");
  const [quantity, setQuantity] = useState("1");
  const [limit, setLimit] = useState("");
  const [trigger, setTrigger] = useState("");
  const [stop, setStop] = useState("");
  const [target, setTarget] = useState("");
  const [error, setError] = useState<string | null>(null);
  const intent = useRef<{ fingerprint: string; key: string } | null>(null);
  const hasLimit = kind === "limit" || kind === "gtt";
  const hasTrigger = kind === "stop_market" || kind === "gtt";
  let estimated: number | null = null;
  try {
    const entry = hasLimit ? paperPrice(limit) : hasTrigger ? paperPrice(trigger) : null;
    const protection = side === "buy" ? paperPrice(stop) : null;
    if (entry && protection && entry > protection && /^\d{1,7}$/.test(quantity))
      estimated = (entry - protection) * Number(quantity);
  } catch {
    // The form shows detailed validation on submit.
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      const ticker = input.trim().toUpperCase();
      const order: Order = {
        symbol: ticker.includes(":") ? ticker : `NSE:${ticker}`,
        side,
        kind,
        quantity: Number(quantity),
        limit_paisa: hasLimit ? paperPrice(limit) : null,
        trigger_paisa: hasTrigger ? paperPrice(trigger) : null,
        risk_stop_paisa: side === "buy" ? paperPrice(stop) : null,
        risk_target_paisa: side === "buy" ? paperPrice(target) : null,
      };
      const fingerprint = JSON.stringify(order);
      if (intent.current?.fingerprint !== fingerprint)
        intent.current = { fingerprint, key: crypto.randomUUID() };
      if (await queue(intent.current.key, order)) intent.current = null;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Check the order fields.");
    }
  }

  return (
    <form onSubmit={submit} className={`${PANEL} grid gap-3`}>
      <h2 className="font-semibold">Queue a paper order</h2>
      <label className={label}>
        NSE symbol
        <input
          value={input}
          className={field}
          maxLength={68}
          list="paper-equity-symbols"
          onChange={(e) => setInput(e.target.value)}
          required
        />
        <datalist id="paper-equity-symbols">
          {symbols.data
            ?.filter((item) => item.exchange === "NSE" && item.kind === "equity")
            .map((item) => (
              <option key={item.symbol} value={item.symbol} />
            ))}
        </datalist>
      </label>
      {symbols.error && (
        <p className="text-xs text-muted-foreground">
          Stock search is unavailable. Enter an NSE symbol manually.
        </p>
      )}
      <div className="grid grid-cols-2 gap-2">
        <label className={label}>
          Side
          <select
            className={field}
            value={side}
            onChange={(e) => setSide(e.target.value as PaperSide)}
          >
            <option value="buy">Buy</option>
            <option value="sell">Sell holdings</option>
          </select>
        </label>
        <label className={label}>
          Shares
          <input
            className={field}
            type="number"
            min="1"
            max="1000000"
            step="1"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            required
          />
        </label>
      </div>
      <label className={label}>
        Order type
        <select
          className={field}
          value={kind}
          onChange={(e) => setKind(e.target.value as PaperKind)}
        >
          {Object.entries(KINDS).map(([key, name]) => (
            <option key={key} value={key}>
              {name}
            </option>
          ))}
        </select>
      </label>
      {hasLimit && (
        <label className={label}>
          Limit price (₹)
          <input
            className={field}
            inputMode="decimal"
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
            required
          />
        </label>
      )}
      {hasTrigger && (
        <label className={label}>
          Trigger price (₹)
          <input
            className={field}
            inputMode="decimal"
            value={trigger}
            onChange={(e) => setTrigger(e.target.value)}
            required
          />
        </label>
      )}
      {side === "buy" && (
        <div className="grid grid-cols-2 gap-2">
          <label className={label}>
            Risk stop (₹)
            <input
              className={field}
              inputMode="decimal"
              value={stop}
              onChange={(e) => setStop(e.target.value)}
              required={account.account.mode === "realistic"}
            />
          </label>
          <label className={label}>
            Risk target (₹)
            <input
              className={field}
              inputMode="decimal"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              required={account.account.mode === "realistic"}
            />
          </label>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        {estimated !== null ? `Indicative stop risk ${formatRupees(estimated)} before costs. ` : ""}
        Risk levels are used for sizing checks. Queue a sell order to close a holding.
      </p>
      <p className="text-xs text-muted-foreground">
        Modelled slippage {account.slippageBps} bps · Up to {formatPct(account.volumeBps / 100, 2)}{" "}
        of session volume · Simulated fees
      </p>
      {error && (
        <p role="alert" className="text-sm text-loss">
          {error}
        </p>
      )}
      <Button type="submit" disabled={locked}>
        Queue paper order
      </Button>
    </form>
  );
}

function DataTable({
  title,
  columns,
  empty,
  children,
}: {
  title: string;
  columns: string[];
  empty: string;
  children: ReactNode[];
}) {
  return (
    <section className={PANEL} aria-label={title}>
      <h2 className="mb-2 font-semibold">{title}</h2>
      {children.length === 0 ? (
        <p className="text-sm text-muted-foreground">{empty}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm tabular-nums [&_td]:px-2 [&_td]:py-2 [&_tr]:border-b">
            <thead className="text-xs text-muted-foreground">
              <tr>
                {columns.map((name) => (
                  <th className="px-2 pb-2" key={name}>
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>{children}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}
