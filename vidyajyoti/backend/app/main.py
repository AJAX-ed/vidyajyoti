from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routers import health, exams, onboarding


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Best-effort table creation; app still starts if DB is down.
    try:
        await init_db()
    except Exception:
        pass
    yield


app = FastAPI(title="VidyaJyoti API", version="1.0.0", lifespan=lifespan)

# CORS — allow the Vite dev server on port 3000
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(exams.router)
app.include_router(onboarding.router)

# Later modules (stubs reserved): battlegrounds.py, doubts.py, points.py


@app.get("/")
async def root():
    return {"app": "VidyaJyoti API", "docs": "/docs", "health": "/api/health"}
