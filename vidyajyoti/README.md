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
