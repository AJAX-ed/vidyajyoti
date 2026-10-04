"""VidyaJyoti ML service — 100% self-hosted models, NO external AI APIs.

Run: uvicorn app_ml.main:app --host 0.0.0.0 --port 9000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app_ml.routes_ml import router as ml_router

app = FastAPI(title="VidyaJyoti ML Service (self-hosted)", version="1.0.0")

# Only internal callers (the main backend) use this service.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ml_router)


@app.get("/")
async def root():
    return {"service": "vidyajyoti-ml", "external_ai_apis": False, "docs": "/docs"}
