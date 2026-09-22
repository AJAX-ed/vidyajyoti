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

## Setup Instructions

### Prerequisites
- Node.js 18+ (LTS recommended)
- Python 3.11+
- PostgreSQL 14+
- Git

### 1. Database Setup

```bash
# Connect to PostgreSQL
psql -U postgres

# Create database and user
CREATE DATABASE vidyajyoti;
CREATE USER vj_user WITH PASSWORD 'vj_password';
GRANT ALL PRIVILEGES ON DATABASE vidyajyoti TO vj_user;

# Enable pgvector extension (optional, for ML service)
\c vidyajyoti
CREATE EXTENSION IF NOT EXISTS vector;
```

### 2. Backend Setup

```bash
cd vidyajyoti/backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables (or edit .env)
export DATABASE_URL="postgresql+asyncpg://vj_user:vj_password@localhost:5432/vidyajyoti"

# Run the backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Backend will be available at: http://localhost:8000
API docs at: http://localhost:8000/docs

### 3. Frontend Setup

```bash
cd vidyajyoti/frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

Frontend will be available at: http://localhost:3000

### 4. ML Service Setup (Optional)

```bash
cd vidyajyoti/backend_ml

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run ML service
uvicorn app_ml.main:app --host 0.0.0.0 --port 9000 --reload
```

ML service will be available at: http://localhost:9000

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
