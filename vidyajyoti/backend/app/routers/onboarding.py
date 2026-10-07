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
    user.onboarding_data = req.data
    user.day_plan = [slot.model_dump() for slot in req.plan]

    # ---- Self-hosted ML: weekly/monthly goals (not exhaustive; year-end pace)
    plan_slots = [s.model_dump() for s in req.plan]
    study_min = sum(s["end"] - s["start"] for s in plan_slots if s["type"] == "study")
    subjects = _subjects_from_data(req.data or {})
    goals = await _ml_call("plan-goals", {
        "user_id": req.user_id,
        "grade": (req.data or {}).get("grade", "11"),
        "subjects": subjects,
        "exams": (req.data or {}).get("exams", []),
        "daily_study_minutes": max(study_min, 60),
        "today": _date.today().isoformat(),
    })
    weekly = (goals or {}).get("weekly_goals")
    monthly = (goals or {}).get("monthly_goals")
    if weekly:
        user.weekly_goals = weekly
        user.monthly_goals = monthly

    # ---- Persist TODAY's daily plan row (future days regenerate fresh) -----
    focus = []
    if weekly:
        focus = weekly[0].get("topics", [])[:4]
    varied = await _ml_call("daily-plan", {
        "user_id": req.user_id,
        "base_plan": plan_slots,
        "focus_topics": focus,
        "date": _date.today().isoformat(),
        "peak_productivity": (req.data or {}).get("peakProductivity"),
    })
    today = _date.today()
    existing = await db.execute(
        select(DailyPlan).where(DailyPlan.user_id == req.user_id,
                                DailyPlan.plan_date == today))
    row = existing.scalar_one_or_none()
    slots_out = (varied or {}).get("plan", plan_slots)
    if row is None:
        row = DailyPlan(user_id=req.user_id, plan_date=today, week_index=1)
        db.add(row)
    row.slots = slots_out
    row.focus_topics = (varied or {}).get("focus_topics", [])
    await db.commit()

    return OnboardingSaveResponse(ok=True, user_id=req.user_id,
                                  slots_saved=len(slots_out))


@router.get("/{user_id}", response_model=OnboardingGetResponse)
async def get_onboarding(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return OnboardingGetResponse(
        onboarded=user.day_plan is not None,
        data=user.onboarding_data,
        plan=user.day_plan,
    )


@router.get("/{user_id}/today")
async def get_today_plan(user_id: int, db: AsyncSession = Depends(get_db)):
    """NEW-DAY LOGIN ENDPOINT — returns a fresh, personalized timetable.

    If today's DailyPlan row already exists we serve it (stable through the
    day). Otherwise we take the student's stored base routine + their current
    week's goals and ask the self-hosted ML service to vary the day — so every
    calendar day looks different (topics rotate, session lengths/order change)
    while wake/sleep/school/meals stay exactly as the student set them."""

    today = _date.today()
    row = (await db.execute(
        select(DailyPlan).where(DailyPlan.user_id == user_id,
                                DailyPlan.plan_date == today)
    )).scalar_one_or_none()
    user = await db.get(User, user_id)

    if row is not None:
        return {
            "user_id": user_id, "date": today.isoformat(), "generated": False,
            "plan": row.slots, "focus_topics": row.focus_topics or [],
            "weekly_goals": (user.weekly_goals if user else None),
            "monthly_goals": (user.monthly_goals if user else None),
        }

    if user is None or not user.day_plan:
        raise HTTPException(status_code=404, detail="Complete onboarding first")

    # ---- Generate a brand-new plan for this new day ------------------------
    weekly = user.weekly_goals or []
    days_since_start = 0
    if weekly:
        try:
            start = _date.fromisoformat(weekly[0]["start"])
            days_since_start = max(0, (today - start).days)
        except Exception:
            pass
    week_idx = min(len(weekly) - 1, days_since_start // 7) if weekly else 0
    week_topics = weekly[week_idx].get("topics", [])[:5] if weekly else []

    varied = await _ml_call("daily-plan", {
        "user_id": user_id,
        "base_plan": user.day_plan,
        "focus_topics": week_topics,
        "date": today.isoformat(),
        "peak_productivity": (user.onboarding_data or {}).get("peakProductivity"),
    })
    slots_out = (varied or {}).get("plan", user.day_plan)
    focus_out = (varied or {}).get("focus_topics", [])

    new_row = DailyPlan(user_id=user_id, plan_date=today,
                        slots=slots_out, focus_topics=focus_out,
                        week_index=week_idx + 1)
    db.add(new_row)
    await db.commit()

    return {
        "user_id": user_id, "date": today.isoformat(), "generated": True,
        "plan": slots_out, "focus_topics": focus_out,
        "weekly_goals": weekly, "monthly_goals": user.monthly_goals,
        "model": "daily-variation-v1 (self-hosted)",
    }
