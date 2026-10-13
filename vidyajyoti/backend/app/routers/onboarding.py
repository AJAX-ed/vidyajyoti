import os
from datetime import date as _date

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import User, DailyPlan
from app.schemas import OnboardingSaveRequest, OnboardingSaveResponse, OnboardingGetResponse

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://localhost:9000").rstrip("/")


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


def _subjects_from_data(data: dict) -> list[str]:
    """Derive study subjects from quiz answers / exam targets. Real syllabus
    data will replace this mapping later (per product spec)."""
    exams = [e.lower() for e in (data.get("exams") or [])]
    if any("jee" in e for e in exams):
        return ["Physics", "Chemistry", "Maths"]
    if any("neet" in e for e in exams):
        return ["Physics", "Chemistry", "Biology"]
    if any("ssc" in e for e in exams):
        return ["Quantitative Aptitude", "Reasoning", "English", "General Awareness"]
    if data.get("grade"):
        return [f"Subject {data['grade']}"]
    return ["General Studies"]


async def _ml_call(path: str, payload: dict, timeout: float = 15.0) -> dict | None:
    """Internal call to the self-hosted ML service. NO external AI APIs."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{ML_SERVICE_URL}/api/ml/{path}", json=payload)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


@router.post("/save", response_model=OnboardingSaveResponse)
async def save_onboarding(req: OnboardingSaveRequest, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, req.user_id)
    if user is None:
        # auto-create demo user so the flow works out of the box
        user = User(id=req.user_id, name="Student", role="student")
        db.add(user)
    # Preserve ALL onboarding fields (wake/sleep/school/meals/prefs are kept as
    # availability & wellbeing inputs for the goal planner — NOT as a timetable).
    user.onboarding_data = req.data

    # ── DEPRECATED legacy behaviour ────────────────────────────────────────
    # The old system stored an hourly "day_plan" of Study Session 1/2 slots.
    # We no longer generate new study-session slots; any plan sent by older
    # clients is stored read-only for history and is NOT used for planning.
    if req.plan:
        user.day_plan = [s.model_dump() for s in req.plan]

    # ── NEW PRIMARY OUTPUT: hierarchical goal plan (deterministic planner) ─
    # Runs locally (rule-planner-v1). No external AI API is called. If no
    # approved syllabus exists it returns `syllabus_pending` and invents
    # nothing; onboarding data is retained while we wait for Segment 2.
    from app.routers.goals import _build_request, plan
    from app import goal_service
    try:
        out = plan(_build_request(user))
        phase = out["status"]
        await goal_service.set_plan_phase(
            db, req.user_id, phase,
            pressure_score=out.get("pressure_score", 0),
            plan_feasibility=out.get("plan_feasibility", "feasible"))
        if phase == "proposed":
            await goal_service.store_planner_output(db, req.user_id, out)
        goals_status = phase
    except Exception:
        # Planner failure must never break login/onboarding persistence.
        await goal_service.set_plan_phase(db, req.user_id, "plan_generating")
        goals_status = "plan_generating"

    await db.commit()
    return OnboardingSaveResponse(ok=True, user_id=req.user_id,
                                  slots_saved=0, plan_status=goals_status)


@router.get("/{user_id}", response_model=OnboardingGetResponse)
async def get_onboarding(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return OnboardingGetResponse(
        onboarded=user.onboarding_data is not None,
        data=user.onboarding_data,
        plan=user.day_plan,   # legacy read-only; new clients use /api/goals
    )


@router.get("/{user_id}/today")
async def get_today_plan(user_id: int, db: AsyncSession = Depends(get_db)):
    """DEPRECATED fixed-timetable endpoint.

    Kept temporarily for older clients. It no longer generates new study
    sessions — it serves only the stored legacy routine blocks (read-only).
    New planning output lives at GET /api/goals/{user_id}/today."""
    today = _date.today()
    row = (await db.execute(
        select(DailyPlan).where(DailyPlan.user_id == user_id,
                                DailyPlan.plan_date == today)
    )).scalar_one_or_none()
    user = await db.get(User, user_id)

    if row is not None:
        return {"deprecated": True, "user_id": user_id, "date": today.isoformat(),
                "generated": False, "plan": row.slots,
                "focus_topics": row.focus_topics or []}

    if user is None or not user.day_plan:
        raise HTTPException(status_code=404,
                            detail="Complete onboarding first; use /api/goals for your learning plan")

    # Serve the stored ROUTINE baseline unchanged (no new study-session fill).
    new_row = DailyPlan(user_id=user_id, plan_date=today,
                        slots=user.day_plan, focus_topics=[], week_index=1)
    db.add(new_row)
    await db.commit()
    return {"deprecated": True, "user_id": user_id, "date": today.isoformat(),
            "generated": True, "plan": user.day_plan, "focus_topics": [],
            "note": "Fixed timetables are deprecated — see /api/goals/{id}/today"}
