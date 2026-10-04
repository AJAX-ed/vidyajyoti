# VidyaJyoti - Indian Exam Preparation Platform

A full-stack web application for Indian students preparing for competitive exams (JEE, NEET, CBSE, SSC, etc.).

## 🚫 NO EXTERNAL AI APIs

**Important:** This project does NOT use any external AI APIs (OpenAI, Anthropic, Gemini, etc.). All AI/ML functionality is implemented using self-hosted, open-source models running on our own servers.

## Tech Stack

### Frontend
- **Runtime:** Node.js (latest LTS)
- **Build Tool:** Vite
- **Framework:** React 19 (function components, hooks)
- **Language:** TypeScript
- **Styling:** Tailwind CSS v4 with `@tailwindcss/vite` plugin
- **Icons:** lucide-react
- **Animations:** framer-motion
- **Notifications:** sonner

### Backend
- **Framework:** FastAPI (Python 3.11+)
- **Server:** uvicorn
- **ORM:** SQLAlchemy 2 (async)
- **Data Validation:** Pydantic
- **Database:** PostgreSQL 14+
- **DB Driver:** asyncpg

### ML Service (Self-Hosted AI)
- **Frameworks:** PyTorch, Hugging Face Transformers, scikit-learn
- **Embeddings:** sentence-transformers
- **Vector Store:** FAISS / pgvector
- **Serving:** FastAPI

## Project Structure

```
vidyajyoti/
├── frontend/                 # React + Vite frontend (port 3000)
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── index.css
│       └── views/
│           ├── LoginPage.tsx
│           ├── OnboardingQuiz.tsx
│           └── Dashboard.tsx
│
├── backend/                  # FastAPI backend (port 8000)
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       ├── database.py
│       ├── models.py
│       ├── schemas.py
│       └── routers/
│           ├── health.py
│           ├── exams.py
│           ├── onboarding.py
│           ├── battlegrounds.py (placeholder)
│           └── doubts.py (placeholder)
│
├── backend_ml/               # Self-hosted ML service (port 9000)
│   ├── requirements.txt
│   └── app_ml/
│       ├── main.py
│       ├── models_ml.py      # ML model classes
│       └── vector_store.py   # FAISS/pgvector integration
│
└── .env                      # Environment variables
```

## Features

### 1. Onboarding Quiz
Students answer detailed questions about:
- Education type (School, Home School, Coaching)
- Grade and target exams
- Morning routine tasks
- Daily schedule (wake/sleep times, school hours, commute)
- Meal times
- Study preferences (session length, break length, peak productivity)

### 2. Rule-Based Day Plan Generator
The system automatically generates a personalized daily schedule:
- Converts all times to minutes for calculation
- Creates fixed blocks for meals, school, coaching, commute, routine
- Fills gaps between fixed blocks with study sessions and breaks
- Handles edge cases (short gaps, overlapping times)
- Outputs a timeline with slot types: study, meal, school, routine, break, sleep, travel

### 3. Dashboard
- Personalized greeting with date
- Points, coins, and streak display
- Quick action buttons
- Today's plan timeline view
- Sidebar navigation (desktop) / Bottom nav (mobile)
- Theme toggle (dark/light)

### 4. Future Features (Placeholders)
- Battlegrounds (gamified quiz battles)
- Doubt resolution (self-hosted RAG tutor)
- Performance analytics
- Points/rewards system
- Community features

## 🏃 How to Run — Step by Step

> ⚡ **Shortcut:** you don't have to do any of this manually. The repo root contains
> `runner.py` — run `python runner.py` (from the folder that contains `vidyajyoti/`) and it will
> install all dependencies, bootstrap PostgreSQL, start backend :8000 + frontend :3000 + ML :9000,
> and show a status dashboard (Ctrl+C stops everything). Flags: `--setup`, `--no-ml`,
> `--backend-only`, `--frontend-only`, `--ml-only`. The steps below are for manual control.

Follow these steps **in order**. You need three services running (PostgreSQL is required; the ML service is optional):

| # | Service | Port | Required? |
|---|---------|------|-----------|
| 1 | PostgreSQL database | 5432 | ✅ Yes |
| 2 | Main backend (FastAPI) | 8000 | ✅ Yes |
| 3 | Frontend (Vite dev server) | 3000 | ✅ Yes |
| 4 | ML service (self-hosted AI) | 9000 | ⚪ Optional |

### Prerequisites
- **Node.js 18+** (latest LTS recommended) — check with `node -v`
- **Python 3.11+** — check with `python3 --version`
- **PostgreSQL 14+** running locally — check with `psql --version`
- **Git**

First, clone/download the project and enter the folder:

```bash
git clone <your-repo-url> vidyajyoti-app
cd vidyajyoti-app/vidyajyoti    # the project root that contains frontend/, backend/, backend_ml/, .env
```

---

### STEP 1 — Set Up PostgreSQL (required)

Start PostgreSQL if it isn't running, then connect as a superuser:

```bash
# macOS (Homebrew)
brew services start postgresql@14

# Ubuntu/Debian
sudo service postgresql start

# Then connect
psql -U postgres        # on Linux you may need: sudo -u postgres psql
```

Inside the `psql` prompt, create the database and user:

```sql
CREATE USER vj_user WITH PASSWORD 'vj_password';
CREATE DATABASE vidyajyoti OWNER vj_user;
\c vidyajyoti
GRANT ALL ON SCHEMA public TO vj_user;

-- Optional: enable pgvector (only needed later for the ML service's persistent vector store)
CREATE EXTENSION IF NOT EXISTS vector;

\q
```

Verify the connection string in `.env` (project root) matches what you created:

```bash
cat .env
# DATABASE_URL=postgresql+asyncpg://vj_user:vj_password@localhost:5432/vidyajyoti
# ML_SERVICE_URL=http://localhost:9000
```

> 💡 Tip: instead of typing SQL manually, you can run this one-liner from the shell:
> ```bash
> psql -U postgres -c "CREATE USER vj_user WITH PASSWORD 'vj_password';" \
>   && psql -U postgres -c "CREATE DATABASE vidyajyoti OWNER vj_user;" \
>   && psql -U postgres -d vidyajyoti -c "GRANT ALL ON SCHEMA public TO vj_user;"
> ```

---

### STEP 2 — Run the Main Backend (FastAPI, port 8000)

From the project root:

```bash
cd backend

# Create + activate a virtual environment
python3 -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Point the app at your database (the defaults in code already match .env)
export DATABASE_URL="postgresql+asyncpg://vj_user:vj_password@localhost:5432/vidyajyoti"

# Start the server (from backend/, so the `app.` package resolves)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

✅ Verify it works:

```bash
curl http://localhost:8000/api/health
# → {"status":"ok","time":"...","db":"ok"}
```

- Interactive API docs (Swagger): **http://localhost:8000/docs**
- Exams list: **http://localhost:8000/api/exams**

Leave this terminal running.

---

### STEP 3 — Run the Frontend (React + Vite, port 3000)

Open a **new terminal**, from the project root:

```bash
cd frontend

# Install dependencies
npm install

# Start the dev server (script already sets host 0.0.0.0 and port 3000)
npm run dev
```

✅ Open **http://localhost:3000** in your browser.

Expected flow:
1. **Login page** → click *Login* (mock auth stores `vj_token` in localStorage).
2. **Onboarding quiz** (7 steps) → answer routine/school/meals/study-preference questions.
3. On step 6 you'll see the **generated day plan timeline** → click *Start Learning*.
4. **Dashboard** shows your plan, stats pills, sidebar/bottom nav, and theme toggle.

Leave this terminal running too.

---

### STEP 4 — Run the ML Service (Optional, self-hosted AI, port 9000)

Only needed if you want the AI endpoints (topic recommendations, schedule adjustment, doubt answering). The main app works fully without it (day plans are rule-based).

Open a **third terminal**, from the project root:

```bash
cd backend_ml

# Create + activate a separate virtual environment (heavy ML deps)
python3 -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate

# Install PyTorch / Transformers / FAISS etc. (large download)
pip install -r requirements.txt

# Start the ML service (from backend_ml/, so the `app_ml.` package resolves)
uvicorn app_ml.main:app --host 0.0.0.0 --port 9000 --reload
```

✅ Verify:

```bash
curl http://localhost:9000/health
```

Available self-hosted AI endpoints (called internally by the main backend via `ML_SERVICE_URL`, never by the browser):

```bash
curl -X POST http://localhost:9000/api/ml/recommend-next-topic \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "exam_id": 1}'

curl -X POST http://localhost:9000/api/ml/answer-doubt \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "subject_id": 1, "question": "What is integration?"}'
```

---

### STEP 5 — Quick Sanity Check (all services up)

| Check | URL / command | Expected |
|-------|---------------|----------|
| Frontend | http://localhost:3000 | VidyaJyoti login page |
| Backend health | `curl localhost:8000/api/health` | `"status":"ok"` and `"db":"ok"` |
| Backend docs | http://localhost:8000/docs | Swagger UI |
| Exams | `curl localhost:8000/api/exams` | JSON list of exams |
| ML service (opt.) | `curl localhost:9000/health` | OK response |

### Production Build (Frontend)

```bash
cd frontend
npm run build      # outputs static files to frontend/dist/
npm run preview    # serve the built app locally
```

### Troubleshooting

- **`ERROR: No matching distribution found for asyncpg==0.30.0` (Windows)** → FIXED AT THE SOURCE: all `requirements.txt` files now use flexible version ranges (`asyncpg>=0.29`, etc.) instead of exact pins, because pinned releases like `asyncpg==0.30.0` ship no wheels for some Windows/Python combos. Additionally, `runner.py` upgrades pip inside every fresh venv before installing (old bundled pip can also cause "No matching distribution found"), and if anything still fails it auto-retries package-by-package, skipping only optional packages with no wheel for your system. Re-running `python runner.py` is always safe — it never wipes a working venv. If you previously hit this error, delete the half-built folder `vidyajyoti/backend/.venv` once and re-run.
- **`db: "error"` in /api/health** → PostgreSQL isn't running or credentials mismatch. Re-check STEP 1 and `DATABASE_URL`.
- **`ModuleNotFoundError: app` / `app_ml`** → run uvicorn from inside `backend/` or `backend_ml/` respectively (not from the project root).
- **Port already in use** → find & kill the process (`lsof -i :3000` / `:8000` / `:9000`, on Windows `netstat -ano | findstr :8000`) or change the port in `package.json` script / uvicorn flags. `runner.py` frees stale ports automatically.
- **CORS errors in browser console** → backend must allow `http://localhost:3000` (already configured in `backend/app/main.py`).
- **`pip install` of ML deps is slow/huge** → expected (PyTorch ≈ 2 GB). Skip the ML service with `python runner.py --no-ml` if you don't need AI features yet; `runner.py` also falls back to a minimal core set if heavy packages fail to build.
- **Windows users** → use `venv\Scripts\activate` instead of `source venv/bin/activate`.

## API Endpoints

### Main Backend (port 8000)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Root endpoint |
| GET | `/api/health` | Health check |
| GET | `/api/exams` | Get all exams with subjects |
| POST | `/api/onboarding/save` | Save onboarding data |
| GET | `/api/onboarding/{user_id}` | Get user's onboarding status |

### ML Service (port 9000)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Root endpoint |
| GET | `/health` | Health check |
| POST | `/api/ml/recommend-next-topic` | Get topic recommendations |
| POST | `/api/ml/schedule-adjust` | Adjust study schedule |
| POST | `/api/ml/answer-doubt` | Answer student doubt (RAG) |

## Self-Hosted AI Architecture

### Topic Recommendation Model
- **Type:** Gradient Boosting or Neural Network
- **Training Data:** Past quiz scores, time spent, difficulty ratings
- **Framework:** scikit-learn or PyTorch
- **Input:** User performance history, studied topics
- **Output:** Recommended topics with confidence scores

### Schedule Optimizer
- **Type:** Optimization algorithms + small neural net
- **Purpose:** Optimize study session timing and duration
- **Features:** Peak productivity alignment, subject balancing

### RAG Tutor (Doubt Resolution)
- **Embedding Model:** sentence-transformers/all-MiniLM-L6-v2
- **Vector Store:** FAISS (in-memory) or pgvector (PostgreSQL)
- **Generator:** Fine-tuned T5-small or Phi-2
- **Process:**
  1. Encode question into embedding
  2. Retrieve top-k relevant passages from educational content
  3. Generate answer using retrieved context
  4. Return answer with confidence and sources

**All models are self-hosted. NO external API calls.**

## Development Workflow

1. **Frontend changes:**
   - Edit files in `frontend/src/`
   - Hot reload enabled via Vite
   - Check TypeScript types

2. **Backend changes:**
   - Edit files in `backend/app/`
   - Auto-reload with `--reload` flag
   - Test endpoints via `/docs` Swagger UI

3. **ML model changes:**
   - Edit files in `backend_ml/app_ml/`
   - Train models separately, save to `./models/`
   - Load models on service startup

## Important Notes

1. **Theme System:**
   - Default theme is dark (`class="dark"` on `<html>`)
   - Toggle saves preference to `localStorage["vj_theme"]`
   - CSS variables defined in `index.css`

2. **Authentication (Mock):**
   - Currently uses localStorage for demo
   - Token stored as `vj_token`
   - User name stored as `vj_user_name`
   - Onboarding status stored as `vj_onboarded`

3. **Day Plan Generation:**
   - Runs on frontend when completing onboarding
   - Can optionally save to backend via `/api/onboarding/save`
   - Uses rule-based logic (no AI required for basic version)

## License

© 2024 VidyaJyoti. All rights reserved.

---

## AI Usage Verification Statement

**No external AI APIs are used in this project.**

All AI functionality is implemented via:
- Self-hosted open-source models (PyTorch, Hugging Face Transformers)
- Local vector stores (FAISS, pgvector)
- Internal HTTP communication between main backend and ML microservice

**Models planned:**
1. Topic recommendation model (scikit-learn/PyTorch)
2. Schedule optimization model (optimization + neural net)
3. RAG-based tutor (sentence-transformers + T5/Phi-2)

**Deployment:**
- ML models run in separate FastAPI service on port 9000
- Main backend calls ML service via internal HTTP
- Frontend never directly accesses AI models

This ensures complete control over AI infrastructure, data privacy, and no dependency on external AI providers.
