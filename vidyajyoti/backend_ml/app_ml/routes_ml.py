"""ML endpoints — all models are SELF-HOSTED (scikit-learn / numpy / optional
local transformers). NO external AI APIs are called anywhere in this file.

Design rule: every endpoint must return a REAL, deterministic result computed
locally, even when only the core dependency tier (numpy + scikit-learn) is
installed. Heavy transformer upgrades are opportunistic and never required.
"""
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/ml", tags=["ml-self-hosted"])

_T0 = time.time()


class RecommendIn(BaseModel):
    user_id: int
    exam_id: int = 0
    stats: dict[str, Any] = {}


class ScheduleIn(BaseModel):
    user_id: int
    plan: list[dict[str, Any]] = []
    history: dict[str, Any] = {}
    session_minutes: int = 50
    break_minutes: int = 10


class DoubtIn(BaseModel):
    user_id: int
    subject_id: int | None = None
    question: str


@router.get("/health")
async def ml_health():
    return {
        "status": "ok",
        "service": "vidyajyoti-ml",
        "version": "1.4.0",
        "external_ai_apis": False,
        "uptime_s": round(time.time() - _T0, 1),
        "time": datetime.now(timezone.utc).isoformat(),
    }


# ------------------------------------------------------------------ recommend
@router.post("/recommend-next-topic")
async def recommend_next_topic(req: RecommendIn):
    """Self-hosted topic recommender.

    Trains a small GradientBoostingClassifier (scikit-learn, in-process) on the
    caller-supplied per-topic stats: mastery score, attempts, average time per
    question vs. the cohort norm, and skip rate. Priority = predicted risk of
    failure x weakness x recency weight. Deterministic given the same input.
    """
    from app_ml.models_ml import recommend_topics
    result = recommend_topics(req.stats or {})
    return {"user_id": req.user_id, "exam_id": req.exam_id, "model": "gbm-recommender-v2", **result}


# ------------------------------------------------------------------ schedule
@router.post("/schedule-adjust")
async def schedule_adjust(req: ScheduleIn):
    """Self-hosted schedule optimizer.

    Constraint solver (pure Python/numpy): keeps school/travel/meals/sleep as
    immovable anchors, then re-packs study sessions so the LONGEST continuous
    blocks land inside the user's peak-productivity window (derived from their
    performance history), inserting breaks between sessions. Never emits meal
    blocks after school-start, and never emits zero study hours unless there
    genuinely is no free gap.
    """
    from app_ml.models_ml import adjust_schedule
    result = adjust_schedule(
        req.plan, req.history,
        session_minutes=req.session_minutes, break_minutes=req.break_minutes,
    )
    return {"user_id": req.user_id, "model": "schedule-optimizer-v2", **result}


# ------------------------------------------------------------------ doubt
@router.post("/answer-doubt")
async def answer_doubt(req: DoubtIn):
    """RAG tutor — fully local.

    Retrieval: TF-IDF/cosine over VidyaJyoti's own notes corpus (FAISS +
    sentence-transformers automatically upgrade recall when installed).
    Generation: extractive composition of top passages; a self-hosted
    flan-t5-small produces a polished summary when available. No network calls.
    """
    from app_ml.vector_store import retrieve_context
    from app_ml.models_ml import compose_answer
    hits = retrieve_context(req.question, k=3)
    answer, sources = compose_answer(hits, req.question)
    return {
        "user_id": req.user_id,
        "subject_id": req.subject_id,
        "question": req.question,
        "answer": answer,
        "sources": sources,
        "model": "rag-tutor-v2 (local retrieval + local generation)",
    }
