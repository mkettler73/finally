# FinAlly — Agent Team Charter

Six specialist agents build FinAlly in parallel against frozen contracts. The
orchestrating session is the **Team Lead**: it owns the contracts, sequences the
waves, routes review findings, and is the only party that may amend a contract.

---

## 1. Roster and file ownership

Ownership is exclusive. **Do not create, edit or delete a file another agent
owns** — if you need a change there, message the Team Lead or the owning agent.
Concurrent edits to a shared file are the single most likely way this build
breaks.

| Agent | Owns | Depends on |
|---|---|---|
| **database-engineer** | `backend/app/db/**`, `backend/tests/db/**` | — |
| **backend-api-engineer** | `backend/app/api/**`, `backend/app/services/**`, `backend/app/main.py`, `backend/pyproject.toml`, `backend/tests/api/**`, `backend/tests/services/**` | data layer |
| **llm-engineer** | `backend/app/llm/**`, `backend/tests/llm/**`, `planning/LLM_NOTES.md` | data layer, trade service |
| **frontend-engineer** | `frontend/**` | HTTP contract only |
| **devops-engineer** | `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `scripts/**`, `db/.gitkeep`, `.env.example` | nothing to start |
| **integration-tester** | `test/**` | everything, at the end |

**Read-only for everyone:** `backend/app/market/**` and its tests. The market data
subsystem is complete and reviewed (`planning/MARKET_DATA_SUMMARY.md`). If you
think it has a bug, report it to the Team Lead — do not patch it.

**Shared-file protocol:**
- `backend/pyproject.toml` is owned by **backend-api-engineer**. Dependencies for
  every backend agent are pre-declared by the Team Lead before the waves start; if
  you need one that is missing, ask rather than edit.
- `backend/app/main.py` is owned by **backend-api-engineer**. The LLM and DB agents
  hand it routers and lifespan hooks to wire in; they do not edit it.
- `planning/API_CONTRACT.md` and `planning/DATA_LAYER.md` are owned by the **Team
  Lead**. Read them; never edit them.

---

## 2. The contracts

Read these before writing a line of code:

| Document | What it fixes |
|---|---|
| `planning/PLAN.md` | Product spec, visual design, architecture, schema |
| `planning/API_CONTRACT.md` | Every HTTP endpoint, exact JSON, error envelope, SSE frame, LLM schema |
| `planning/DATA_LAYER.md` | The `app.db` Python surface, schema details, transaction semantics |
| `planning/MARKET_DATA_SUMMARY.md` | The finished market data subsystem you build on |
| `backend/CLAUDE.md` | Backend dev commands and market-data usage |

The contracts are frozen so that you can build against a counterpart that does not
exist yet. Build to the contract, not to whatever half-finished code you find.

---

## 3. Working standards

**Every agent:**
- Writes unit tests for their own code, in their own test directory, and leaves
  the whole suite green before reporting done. "Done" means tested, not written.
- Runs the linter/formatter for their stack before reporting done.
- Never weakens a test to make it pass. If a test is wrong, say so explicitly in
  your report rather than quietly deleting it.
- Never commits. The Team Lead handles all git operations.
- Reports honestly: if something is unfinished, broken, or you worked around a
  blocker, say so plainly in your final report. A green report over broken code
  costs the team far more than an honest red one.

**Backend (Python):**
```bash
cd backend
uv sync --extra dev
uv run --extra dev pytest -q
uv run --extra dev ruff check app/ tests/ && uv run --extra dev ruff format app/ tests/
```
Python 3.12, `from __future__ import annotations`, full type hints, line length 100.

Two standing rules inherited from the market-data build, which apply to all
backend tests:
1. **Never touch the network.** Mock every external client. This includes the LLM.
2. **Never sleep for a real cadence.** Inject intervals.

And one hard-won lesson: when mocking an external SDK's models, build them from
the **real class**, not a bare `MagicMock`. A `MagicMock` fabricates whatever
attribute you ask for, which is exactly how a completely broken Massive client
once passed thirteen green tests.

**Frontend (TypeScript):**
Next.js with `output: 'export'`, TypeScript strict, Tailwind. `npm run build` must
produce a clean static export with zero type errors.

---

## 4. Definition of done

The build is complete when:
1. `cd backend && uv run --extra dev pytest -q` is green.
2. `cd frontend && npm run build` produces a static export with no type errors.
3. `docker build .` succeeds and the container serves the UI on port 8000.
4. The Playwright E2E suite in `test/` passes against the built container.
5. A user can: see ten streaming tickers, buy, sell, watch the portfolio update,
   add/remove a watchlist ticker, and have the AI execute a trade via chat.

---

## 5. Communication

Report to the Team Lead when you finish, or immediately if you are blocked. Your
final report should state, tersely:
- What you built (files, endpoints, components).
- Test counts and the actual command output proving they pass.
- Anything you could not finish, and why.
- Any contract ambiguity you hit and the assumption you made.

Do not spawn further subagents. Do not run `git commit`, `git push`, or `docker
run` against the user's machine without being asked to.

---

## 6. Environment notes for this machine

- **Windows 11**, PowerShell primary; a Bash tool is also available. Watch path
  separators in scripts and config.
- **TLS interception on outbound package fetches.** Plain `uv sync` fails with
  `invalid peer certificate: UnknownIssuer`. `UV_NATIVE_TLS=1` is set in
  `.claude/settings.json`, which fixes it; if you still hit it, pass
  `--system-certs` explicitly. `npm` is unaffected.
- Toolchain present: `uv 0.12.7`, `node v24.19.0`, `npm 11.17.0`, `docker 29.7.2`.
- Backend dependencies are already resolved and locked, including `litellm`,
  `pydantic`, `python-dotenv` and `pytest-mock`. Run `uv sync --extra dev` and go.
