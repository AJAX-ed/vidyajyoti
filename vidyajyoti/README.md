# 🪔 VidyaJyoti — One-Click Full-Stack Exam-Prep App

**Everything runs from ONE file: `runner.py`.** It auto-installs missing tools
(Python modules, Node.js/npm, PostgreSQL), builds dependencies, creates the DB
schema, and starts all three services. No external AI APIs — all ML is self-hosted.

## Requirements
Only **Python 3.10–3.12** on PATH. (Windows note: avoid Python 3.14 — some wheels
like asyncpg/psycopg2 aren't published for it yet; use `py -3.12 runner.py`.)

## Run
```powershell
python runner.py            # installs + starts backend :8000, frontend :3000, ML :9000
python runner.py --setup    # install/check only, don't start servers
python runner.py --no-ml    # skip the ML service
python runner.py --backend-only | --frontend-only | --ml-only
```
Flags: `--no-install` skips system-level installs (Node/PostgreSQL) if you prefer manual setup.

What happens automatically:
1. **STEP 0** – pip-installs helper modules (`psutil`, `psycopg2-binary`) if missing.
2. **STEP 1** – finds or installs Node.js/npm (winget/choco/MSI on Windows, apt/dnf on Linux, brew on macOS), then `npm install` in `frontend/`.
3. **STEP 2** – creates `.venv` per service and pip-installs `requirements.txt` (prebuilt wheels only → no `pg_config` build errors).
4. **STEP 3** – installs/starts PostgreSQL if absent (apt/brew/winget/choco/Docker fallback), creates role `vj_user`, database `vidyajyoti`, grants, pgvector (optional), then pre-creates tables. If Postgres can't be reached, the app still starts with a clear warning.
5. **STEP 4** – launches all servers as background processes, logs to `.vj_logs/`, opens http://localhost:3000. **Ctrl+C stops everything cleanly.**

## URLs
| Service | URL |
|---|---|
| Frontend (open this) | http://localhost:3000 |
| Backend health | http://localhost:8000/api/health |
| Swagger docs | http://localhost:8000/docs |
| Self-hosted ML | http://localhost:9000/api/ml/health |

## Troubleshooting
- **ML service stuck "STARTING" forever** → fixed in current `runner.py`: the server process no longer pip-installs anything or imports heavy ML libraries at startup (that was blocking uvicorn from ever binding). It boots in ~1 s with pure-Python fallbacks; torch/faiss/sentence-transformers install in a background thread and are picked up per-request when ready. Readiness is now verified by an actual HTTP call to `/api/ml/health`, not just an open port. On Windows, `winerror 10061 "connection refused"` means the service never really bound — the runner now pre-flight-imports `app_ml.main` and prints the real traceback before launching uvicorn, so you see the true cause instead of a hang. Note: if disk space is low (<5 GB), the *heavy* model packages are intentionally skipped; the service still runs fully on self-hosted pure-Python/heuristic models (check `.vj_logs/ml_heavy_install.log`).
- **Blank white screen at http://localhost:3000** → fixed four ways: (1) the runner fetches the served HTML and verifies it contains `#root` AND that `/src/main.tsx` actually compiles (HTTP 200) before declaring the frontend UP — a Vite server serving a broken bundle is reported as failed, not "UP"; (2) `main.tsx` wraps the app in an ErrorBoundary, so any React runtime error is displayed on-page with the stack trace instead of rendering nothing; (3) an 8-second watchdog in `main.tsx` replaces the boot splash with an actionable "JS did not start" message (with reload button) if the bundle never executes; (4) STEP 1 now performs a deep integrity check of `node_modules` (vite binary, react, tailwind, framer-motion, lucide, sonner AND the platform-specific rollup native package) — if anything is missing or corrupted (the classic cause after an interrupted/crashed npm install, e.g. `MODULE_NOT_FOUND` from `rollup/dist/native.js`), the runner deletes and reinstalls automatically. Manual recovery if all else fails: delete `frontend/node_modules` + `frontend/package-lock.json`, then re-run `python runner.py`. Also: keep the project OUT of OneDrive-synced folders (file locking corrupts node_modules mid-install).
- **`'npm.cmd' is not recognized` / frontend STOPPED** → fixed in current `runner.py`: it now launches Vite directly via `node node_modules/vite/bin/vite.js` (absolute paths, no cmd quoting bugs), with npm as fallback. If you see rollup `MODULE_NOT_FOUND`, delete `frontend/node_modules` and re-run — the runner will reinstall.
- **pip source-build errors (pg_config)** → already prevented: requirements use version ranges so pip picks prebuilt wheels; venv stamp forces clean reinstall when needed.
- **PostgreSQL won't install silently on Windows** → run once manually: `winget install -e --accept-package-agreements PostgreSQL.16` (the installer prompts for a superuser password; set `PGPASSWORD` before `python runner.py` so bootstrap can connect), or use Docker: `docker run -d --name vj-pg -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16`.
- **Port busy** → the runner kills stale listeners on 3000/8000/9000 before starting.
- Check `.vj_logs/*.log` for each service.

## Structure
```
runner.py            ← single entry point
frontend/            React 19 + Vite + TS + Tailwind 4 (:3000)
backend/             FastAPI + SQLAlchemy(async) + PostgreSQL (:8000)
backend_ml/          Self-hosted ML service — sklearn GBM recommender, FAISS +
                     sentence-transformers + flan-t5-small RAG tutor (:9000)
.env                 DATABASE_URL / ML_SERVICE_URL
DESIGN_AND_BUILD.md  Full design doc (Part 1 + Part 2 AI audit)
```
