# AGENTS.md

Notes for coding agents and people working in this repo.

## Stack

- Backend: Python 3.14 (uv), FastAPI, one uvicorn worker, Pydantic v2, stdlib `sqlite3` with SQL migrations, argon2-cffi, anthropic SDK. Own asyncio scheduler, no ORM.
- Frontend: React 19, TypeScript, Vite, Tailwind CSS 4, Radix, TanStack Query, React Router 7, Recharts. Node 24, npm.
- Tests: pytest, ruff, Vitest, Testing Library.
- Hosting: one Docker container behind an HTTPS proxy, on the home network.

## Commands

Always run uv with `UV_FROZEN=1`.

| Task | Command |
|------|---------|
| Install | `cd backend && UV_FROZEN=1 uv sync --frozen` and `cd frontend && npm ci` |
| Run locally | `scripts/dev.sh` |
| All checks | `scripts/check.sh` |
| Backend tests | `cd backend && UV_FROZEN=1 uv run pytest -q` |
| Frontend tests | `cd frontend && npm test` and `npm run typecheck` |
| Regenerate API types | `scripts/gen_api_types.sh` |
| Regenerate import schema | `cd backend && UV_FROZEN=1 uv run python ../scripts/gen_schema.py` |
| Regenerate samples | `cd backend && UV_FROZEN=1 uv run python ../scripts/make_samples.py` |
| Privacy scan | `UV_FROZEN=1 uv run --project backend python scripts/scan_repo.py` |

## Layout

- `backend/app/`: `api/` (one router per screen), `services/` (numbers for each screen), `importer/` (verify, validate, sanitize, apply runs), `db/` (write lock, migrations), `auth/`, `market/`, `valuations/`, `ai/`, `jobs/`, `demo/`.
- `frontend/src/`: `routes/`, `features/<screen>/`, `components/`, `lib/api/` (`schema.ts` is generated).
- `schema/`: the import format, its generated JSON schema, categories, redaction rules and `check_run.py`.
- `samples/`: a fake household. Every test uses it.

## Rules

- Never commit real financial data, databases, env files or keys.
- No full account or card numbers anywhere. Last four digits at most.
- SQLite tables are `STRICT`. Writes go through the one process-wide write lock.
- Logs are JSON and never hold values (amounts, names, keys).
- Don't hand-edit generated files: `backend/uv.lock`, `frontend/package-lock.json`, `frontend/src/lib/api/schema.ts`, `schema/import-v1.schema.json`, `samples/data/`.
