# VidyaJyoti

Full-stack Indian exam-prep platform (React + Vite frontend, FastAPI + PostgreSQL backend, self-hosted ML service).

## 🚀 One-Click Run (Recommended)

A `runner.py` script sits in the repository root. It installs everything and starts all servers for you:

```bash
python runner.py            # start backend + frontend (+ ML service)
```

It automatically:
1. Installs missing Python modules (`psutil`, `psycopg2-binary`) via `pip`.
2. Runs `npm install` if `frontend/node_modules` is missing.
3. Creates `.venv`s and pip-installs `backend/requirements.txt` (and ML requirements).
4. Bootstraps PostgreSQL — creates role `vj_user`, database `vidyajyoti`, schema grants (only if missing; prints manual SQL if it can't connect).
5. Launches **backend :8000**, **frontend :3000**, **ML service :9000** as background processes with logs in `.vj_logs/`.
6. Shows a live status dashboard. Press **Ctrl+C** to stop everything cleanly.

Useful flags:

| Command | Effect |
|---|---|
| `python runner.py --setup` | Only install deps / check env, don't start servers |
| `python runner.py --no-ml` | Skip the ML service (port 9000) |
| `python runner.py --backend-only` / `--frontend-only` / `--ml-only` | Start a single service |

Then open **http://localhost:3000**. API docs: http://localhost:8000/docs.

---

## Manual Step-by-Step Run

All setup and run instructions live in **[vidyajyoti/README.md](vidyajyoti/README.md)**.

TL;DR (run each service from the `vidyajyoti/` project root):

1. **PostgreSQL** – create user/db: `vj_user` / `vidyajyoti` (see STEP 1)
2. **Backend (port 8000)** – `cd backend && pip install -r requirements.txt && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
3. **Frontend (port 3000)** – `cd frontend && npm install && npm run dev`
4. **ML service (port 9000, optional)** – `cd backend_ml && pip install -r requirements.txt && uvicorn app_ml.main:app --host 0.0.0.0 --port 9000 --reload`

Then open http://localhost:3000.

> 🚫 No external AI APIs are used — all AI runs on our own self-hosted models. See the "AI Usage Verification Statement" in `vidyajyoti/README.md`.
