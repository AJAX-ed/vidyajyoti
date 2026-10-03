# VidyaJyoti

Full-stack Indian exam-prep platform (React + Vite frontend, FastAPI + PostgreSQL backend, self-hosted ML service).

## Quick Start (step by step)

All setup and run instructions live in **[vidyajyoti/README.md](vidyajyoti/README.md)**.

TL;DR (run each service from the `vidyajyoti/` project root):

1. **PostgreSQL** – create user/db: `vj_user` / `vidyajyoti` (see STEP 1)
2. **Backend (port 8000)** – `cd backend && pip install -r requirements.txt && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
3. **Frontend (port 3000)** – `cd frontend && npm install && npm run dev`
4. **ML service (port 9000, optional)** – `cd backend_ml && pip install -r requirements.txt && uvicorn app_ml.main:app --host 0.0.0.0 --port 9000 --reload`

Then open http://localhost:3000.

> 🚫 No external AI APIs are used — all AI runs on our own self-hosted models. See the "AI Usage Verification Statement" in `vidyajyoti/README.md`.
