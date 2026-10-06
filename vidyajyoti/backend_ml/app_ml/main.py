"""VidyaJyoti ML service — 100% self-hosted models, NO external AI APIs.

Run: uvicorn app_ml.main:app --host 0.0.0.0 --port 9000

Startup policy (hard rules learned from the "stuck in STARTING forever" incident):
  1. Heavy libraries (torch/faiss/sentence-transformers) are NEVER imported at
     module level and NEVER pip-installed inside the server process. That used
     to run multi-GB downloads while uvicorn was still binding :9000, so health
     probes timed out forever and the dashboard showed the service as never
     running. This module imports ONLY fastapi + stdlib at boot (<1 s).
  2. requirements.txt lists only what is needed to START (fastapi/uvicorn/
     pydantic/numpy/scikit-learn). Heavy transformer stack is OPTIONAL and is
     installed by runner.py in a background thread with a Python-version-aware
     package matrix. Until it lands, every endpoint serves deterministic
     pure-Python self-hosted fallbacks.
  3. /api/ml/health is implemented HERE with zero extra imports, so the runner
     can always distinguish "service down" from "heavy models not installed".
"""
import importlib.util
import time
from datetime import datetime, timezone

from fastapi import FastAPI

_START_TS = time.time()


def _has(mod: str) -> bool:
    """True if a module is importable WITHOUT actually importing it (<1 ms)."""
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


# Snapshot of which self-hosted model stacks are available right now.
CAPABILITIES = {
    "sklearn": _has("sklearn"),
    "numpy": _has("numpy"),
    "faiss": _has("faiss"),
    "sentence_transformers": _has("sentence_transformers"),
    "torch": _has("torch"),
    "external_ai_apis": False,  # GUARANTEED: no external AI APIs anywhere.
}

app = FastAPI(title="VidyaJyoti ML Service (self-hosted)", version="1.3.0")


@app.get("/health")
async def liveness():
    """Pure liveness probe — no dependencies, answers immediately."""
    return {"status": "ok", "uptime_s": round(time.time() - _START_TS, 1)}


@app.get("/api/ml/health")
async def ml_health_core():
    """Readiness probe used by runner.py — implemented HERE with only stdlib
    + fastapi so it ALWAYS answers, even if routes_ml fails to import."""
    return {
        "status": "ok",
        "service": "vidyajyoti-ml",
        "version": "1.3.0",
        "external_ai_apis": False,
        "capabilities": CAPABILITIES,
        "time": datetime.now(timezone.utc).isoformat(),
    }


# Mount the full ML router LAST and defensively: if it ever fails to import
# (bad edit, missing optional dep), the service still starts and still
# answers both health probes instead of hanging/failing at boot.
try:
    from fastapi.middleware.cors import CORSMiddleware
    from app_ml.routes_ml import router as ml_router

    # Internal callers only (main backend on :8000 + local debugging).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8000", "http://127.0.0.1:8000",
                       "http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(ml_router)
    ROUTER_ERROR = None
except Exception as exc:  # pragma: no cover — defensive
    ROUTER_ERROR = repr(exc)

    @app.on_event("startup")
    async def _report_router_failure():  # logs the real cause into ml_service.log
        print(f"[ml_service] WARNING: routes_ml failed to import: {ROUTER_ERROR}")


@app.get("/api/ml/routes-status")
async def routes_status():
    """Diagnostics: did the ML router mount? If not, why. Helps spot a
    half-started service instantly instead of guessing."""
    return {"router_mounted": ROUTER_ERROR is None,
            "error": ROUTER_ERROR,
            "endpoints": [r.path for r in app.routes]}
