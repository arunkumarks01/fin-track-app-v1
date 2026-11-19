# FinTrack v2 — Tooling & Reusable Assets

> Created 2026-11-19. (a) Which MCP servers / skills are genuinely useful for this build,
> and (b) the v1 Kite-login flow to port. Honest about what helps vs what's noise.

## A. MCP servers

**Useful for this build:**
| Server | Use |
|---|---|
| **drawio** | Generate architecture / data-flow / sequence / ER diagrams from the design docs — worth doing now to visualize ARCHITECTURE.md (the pipeline, the data foundation, the Kite OAuth flow). |
| **claude-vscode** (`getDiagnostics`) | Read live TS/Python errors from the editor during implementation — faster than re-running compilers. |

**Configured but NOT relevant** (SAP-domain tooling — ignore for a Python/Next.js NSE app):
`cap` (CAP/CDS), `ui5`, `ui5-webcomponents`, `knowledge-at-sap`, `btp-live`, `fiori`.

**Down right now:** `github-tools` (auth error) — would help PR/issue workflow if fixed; not required.

**Note — there is no Kite/market-data MCP.** Kite is integrated as **direct code** (the
`kiteconnect` lib + the OAuth in `broker/kite/auth.py`), not via MCP. Don't expect an MCP to
fetch market data; it's the extractor + the broker module.

## B. Skills

| Skill | When in the build |
|---|---|
| **init** | Phase 0 — generate the new repo's `CLAUDE.md` |
| **code-review** | Every phase — review the diff at each exit gate (the review discipline) |
| **security-review** | Before Phase 11 (live-adjacent) and after the auth/execution work in Phase 5 |
| **dataviz** | Phase 8 — chart/dashboard design (candles, equity curve, heatmaps, payoff diagrams) |
| **claude-api** | Phase 3 & 8 — the LLM agent layer is Claude-based (model ids, tool-use, caching, token budgeting) |
| **run** | Phase 8 — launch + screenshot the app to verify UX against real screens |
| **arch-lens** | Optional now — turn ARCHITECTURE.md into an interactive HTML viewer to study the design |
| **simplify** | Optional — cleanup passes between phases |
| **workflow-authoring / Workflow** | Optional — only if you opt into multi-agent orchestration for a big parallel phase; overkill otherwise |

## C. Kite login — the v1 flow to port (it's good; reuse it)

**Source files in v1** (`/Users/I524409/local-apps/fin-track-app`):
- `backend/broker/kite/auth.py` — the OAuth core (port this).
- `backend/api/kite_auth.py` — the endpoints.
- `frontend/components/settings/KiteConnectTab.tsx` — the key/secret form + Connect button.
- `backend/main.py` lifespan — restores the token on startup.

**The flow (reuse as-is):**
1. User enters API key/secret (Settings) → `POST /api/kite/configure` saves them + hot-reloads.
2. `GET /api/kite/login-url` → returns `https://kite.zerodha.com/connect/login?v=3&api_key=…`.
3. Frontend opens it; user logs in at Kite; Kite redirects to `GET /api/kite/callback?request_token=…&status=success`.
4. Callback computes **checksum = SHA-256(api_key + request_token + api_secret)**, `POST`s to `api.kite.trade/session/token`, gets the `access_token`, stores it, shows a success HTML page.
5. `GET /api/kite/status` + `GET /api/kite/token-status` report connection + expiry; `POST /api/kite/logout` invalidates.

**Hard Kite constraint (carry into UX):** the access token **expires ~06:00 IST every
morning** — the app **cannot be fully unattended**; a human re-logs-in daily. v1 computes and
surfaces this ("session expires in Nh — log in before 06:00 IST"); v2 keeps that banner
(`KiteSessionBanner`) and a Telegram nudge.

**The ONE change for v2 (per ARCHITECTURE no-Redis decision):**
- v1 stored the token in **Redis** (28h TTL) + the in-memory `settings` singleton. v2 has no
  Redis → store the token in **Postgres** (for restart recovery, like v1's lifespan restore)
  and keep it in **in-process memory** for the hot path. Single-process means the in-memory
  token is reliable (no cross-worker drift — the v1 multi-worker hazard disappears).
- Keep the checksum, endpoints, expiry logic, and UX exactly as v1 — they're correct.

**Minor cleanup for v2:** v1's `/configure` writes the key/secret into `.env` at runtime
(`_write_env_vars`). That works but is a little hacky; v2 can store broker credentials in a
small settings/secrets table instead. Non-blocking.

## D. Recommended external MCP / tooling additions

**Honest reframe first:** quality comes from **standards + tests + review in CI** — not from
adding servers. The biggest levers are already in the plan: golden/regression/parity tests,
CI gates, Ruff+mypy (Python), ESLint+Prettier+`tsc --strict` (TS), and the `code-review` /
`security-review` skills. Wire those into Phase 0. The below *assist* that; they don't replace it.

| Add | For | Priority |
|---|---|---|
| **Playwright MCP** (`@playwright/mcp`) | UI/UX verification — drive a real browser, screenshot, a11y snapshot, responsive checks. Closes v1's "can't see the rendered UI" gap. | **High (Phase 8)** |
| **Context7 MCP** (Upstash) | Pulls *current* library docs into context → agent builds on correct APIs (prevents the v1 `react-table@^9` / stale-rate class of bug). | **High** |
| **Semgrep MCP** | Enforce your own code-standard patterns + security as rules, beyond linters. | Medium |
| Fix **github-tools** (auth broken) | PR-based review workflow so `code-review` posts inline per phase. | Medium |
| **Sentry MCP** | Error tracking — ties into Phase 9 observability. | Medium |

Install interactively (`claude mcp add …`); this session can't. MCP availability/versions move
— verify current when you add them.

## E. Vetted external skills (from the shared links)

Assessed against our locked stack (Python/FastAPI + raw asyncpg, Next.js 15, native VPS
deploy, LLM in the Python backend). **Skills advise; DECISIONS.md governs — never let one
override a locked choice.**

**Adopt:**
- **FastAPI official skill** (`uvx library-skills --skill fastapi`) — version-matched API guidance; prevents API drift. *Ignore its `fastapi deploy`/Cloud nudge — we deploy native.*
- **Vercel `web-design-guidelines`** — 100+ a11y/UX/perf rules; automates the honest-UI checks. Phase 8.
- **Vercel `vercel-react-best-practices` + `composition-patterns`** — perf + clean component composition. Phase 8.
- **A UI-verification skill** — Vercel `next-dev-loop` or `agent-browser` (or Playwright MCP above) — see the rendered UI. Pick one.

**Reference only (take the spirit, not the whole thing):**
- **fastapi-clean-architecture** (rafaelkamimura) — adopt the layering/separation discipline; **SKIP its SQLAlchemy-ORM + dependency-injector IoC + repository ceremony** (conflicts with our raw-asyncpg decision; over-engineers a single-dev app).
- **ui-ux-pro-max** (community) — idea generator for palettes/fonts/UX checklists; unofficial, vet quality; overlaps `dataviz`; must not override UX_DESIGN.md.
- **anthropics/skills** — canonical skill-authoring format + optional doc-export (pdf/xlsx → report/journal export later) and a web-app-testing skill. Not core.

**Skip (not relevant):**
- All Vercel **platform** skills (`deploy-to-vercel`, `vercel-cli`, `vercel-optimize`, `sandbox`, `turborepo`) — we deploy native on a VPS, not Vercel.
- **Vercel AI SDK** (`ai-sdk`/`ai-elements`) — only if you ever do a *client-side* AI UI; our LLM lives in the Python backend. Optional, architecture-mismatch otherwise.

**Caveats:** community skills are unvetted — read the SKILL.md before trusting; most of these
are frontend (Next.js) — only the FastAPI ones touch the backend; can't install in this
non-interactive session (`npx skills add …` / `uvx library-skills` interactively).
