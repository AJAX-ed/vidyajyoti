"""ML proxy router — the ONLY way the frontend reaches AI functionality.

Flow: Frontend → /api/ml/* (this router, :8000) → internal ML microservice
(:9000). The ML service is fully self-hosted; NO external AI APIs are called
anywhere in this chain. If the ML service is down we return 503 with a clear
JSON body so callers can fall back gracefully instead of seeing a raw
{"detail": "Not Found"} from the wrong server.
"""
import os

import httpx
from fastapi import APIRouter, Response

router = APIRouter(prefix="/api/ml", tags=["ml-proxy"])

# Internal-only hop. Default matches .env's ML_SERVICE_URL.
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://localhost:9000").rstrip("/")


async def _proxy(path: str, payload: dict) -> Response:
    url = f"{ML_SERVICE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload)
        if r.status_code == 404 or not r.content:
            # ML service up but route missing (or empty) — never forward a bare
            # "Not Found" to the app; give an explicit, machine-checkable error.
            return Response(
                content='{"ok": false, "error": "ml endpoint unavailable", "path": "%s"}' % path,
                status_code=503, media_type="application/json")
        return Response(content=r.content, status_code=r.status_code,
                        media_type=r.headers.get("content-type", "application/json"))
    except Exception as exc:
        return Response(
            content='{"ok": false, "error": "ml service offline", "detail": "%s"}'
                    % str(exc).replace('"', "'"),
            status_code=503, media_type="application/json")


@router.post("/schedule-adjust")
async def schedule_adjust(payload: dict):
    """Proxy the personalized schedule optimizer. Body is passed through
    verbatim (user_id, wake, sleep, plan, history, peak_productivity,
    session_minutes, break_minutes)."""
    return await _proxy("/api/ml/schedule-adjust", payload)


@router.post("/recommend-next-topic")
async def recommend_next_topic(payload: dict):
    return await _proxy("/api/ml/recommend-next-topic", payload)


@router.post("/answer-doubt")
async def answer_doubt(payload: dict):
    return await _proxy("/api/ml/answer-doubt", payload)


@router.get("/health")
async def ml_health():
    """Expose ML readiness through the main backend so the dashboard and
    runner have one canonical URL to check."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{ML_SERVICE_URL}/api/ml/health")
        return Response(content=r.content, status_code=r.status_code,
                        media_type="application/json")
    except Exception:
        return Response(
            content='{"status": "offline", "external_ai_apis": false}',
            status_code=503, media_type="application/json")


@router.post("/plan-goals")
async def plan_goals(payload: dict):
    """Proxy the self-hosted weekly/monthly pacing planner."""
    return await _proxy("/api/ml/plan-goals", payload)


@router.post("/daily-plan")
async def daily_plan(payload: dict):
    """Proxy the per-day plan variation model (different timetable each day)."""
    return await _proxy("/api/ml/daily-plan", payload)
