# FinTrack v2

Learning-first NSE research, paper-trading and backtesting platform (single user, paper only).
Design and plan: [`docs/`](docs/) — start with `ARCHITECTURE.md` and `BUILD_PLAN.md`.
Developer guide: [`CLAUDE.md`](CLAUDE.md).

```bash
cp .env.example .env            # fill in DB password + API_AUTH_TOKEN
ssh -N -L 5433:localhost:5432 fintrack-vps   # DB tunnel (separate terminal)
cd backend && uv sync && uv run python -m app
cd frontend && npm ci && BACKEND_URL=http://127.0.0.1:8000 npm run dev
```
