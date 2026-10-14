"""Goal-planning API — Segment 1 wiring.

Replaces the OLD fixed daily-timetable flow as the PRIMARY planning output:
onboarding completion now feeds the deterministic goal planner (never an
external AI API) and returns hierarchical monthly / weekly / flexible-daily
goals, or `syllabus_pending` when no approved syllabus version exists.

Routes only receive/validate HTTP; all business rules live in
planner_service / goal_service / goal_validation (backend-enforced).
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.goal_models import Goal
from app import goal_service
from app.planner_service import plan

router = APIRouter(prefix="/api/goals", tags=["goals"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ── Placeholder syllabus until Segment 2 delivers official versions ─────
# NOTE: these are REAL official NCERT/JEE chapter names used as a temporary
# stand-in so the pipeline is testable end-to-end. When Segment 2 publishes
# administrator-approved syllabus versions, this table is replaced by a DB
# lookup. If nothing matches, the planner returns `syllabus_pending` and we
# NEVER invent "Topic 1"-style placeholders.
_PLACEHOLDER_SYLLABUS: dict[str, list[dict]] = {
    "JEE_MAIN": [
        {"item_id": "phy-uam", "subject": "Physics", "title": "Units and Measurements",
         "difficulty": 0.4, "est_minutes": 180, "prerequisites": []},
        {"item_id": "phy-msl", "subject": "Physics", "title": "Motion in a Straight Line",
         "difficulty": 0.5, "est_minutes": 200, "prerequisites": ["phy-uam"]},
        {"item_id": "phy-mp", "subject": "Physics", "title": "Motion in a Plane",
         "difficulty": 0.55, "est_minutes": 220, "prerequisites": ["phy-msl"]},
        {"item_id": "phy-lom", "subject": "Physics", "title": "Laws of Motion",
         "difficulty": 0.6, "est_minutes": 240, "prerequisites": ["phy-mp"]},
        {"item_id": "phy-wep", "subject": "Physics", "title": "Work, Energy and Power",
         "difficulty": 0.55, "est_minutes": 220, "prerequisites": ["phy-lom"]},
        {"item_id": "chem-cbat", "subject": "Chemistry", "title": "Some Basic Concepts of Chemistry",
         "difficulty": 0.45, "est_minutes": 200, "prerequisites": []},
        {"item_id": "chem-cbms", "subject": "Chemistry", "title": "Chemical Bonding and Molecular Structure",
         "difficulty": 0.6, "est_minutes": 240, "prerequisites": ["chem-cbat"]},
        {"item_id": "math-tf", "subject": "Maths", "title": "Trigonometric Functions",
         "difficulty": 0.5, "est_minutes": 210, "prerequisites": []},
        {"item_id": "math-sl", "subject": "Maths", "title": "Sets and Relations",
         "difficulty": 0.4, "est_minutes": 170, "prerequisites": []},
    ],
}


def _exam_code_from(data: dict) -> str:
    exams = [e.lower() for e in (data.get("exams") or [])]
    if any("jee" in e for e in exams):
        return "JEE_MAIN"
    return ""  # no approved mapping → planner will answer syllabus_pending


def _build_request(user, check_in: dict | None = None) -> dict:
    data = user.onboarding_data or {}
    exam_code = _exam_code_from(data)
    syl = _PLACEHOLDER_SYLLABUS.get(exam_code, [])
    availability = {
        "wakeTime": data.get("wakeTime"), "sleepTime": data.get("sleepTime"),
        "schoolStart": data.get("schoolStart"), "schoolEnd": data.get("schoolEnd"),
        "commuteMinutes": data.get("commuteMinutes"),
        "breakfastTime": data.get("breakfastTime"), "lunchTime": data.get("lunchTime"),
        "dinnerTime": data.get("dinnerTime"),
    }
    prefs = {
        "studySessionMinutes": data.get("studySessionMinutes", 45),
        "breakMinutes": data.get("breakMinutes", 10),
        "peakProductivity": data.get("peakProductivity"),
    }
    return {
        "student_id": str(user.id),
        "exam_code": exam_code or "UNKNOWN",
        "target_exam_date": data.get("targetExamDate") or f"{date.today().year}-12-31",
        "syllabus_version_id": "placeholder-v0" if syl else None,
        "approved_syllabus": syl,
        "completed_syllabus_items": [],
        "diagnostic_scores": {},
        "availability": availability,
        "onboarding_preferences": prefs,
        "check_in": check_in,
        "recent_events": [],
        "today": date.today().isoformat(),
    }


@router.post("/generate/{user_id}")
async def generate_plan(user_id: int, db: AsyncSession = Depends(get_db)):
    """Run the deterministic planner for a student (called after onboarding).

    Returns the shared planner output contract. If no approved syllabus
    exists → status `syllabus_pending` (no fake academic goals are created).
    """
    from app.models import User
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found — complete onboarding first")

    out = plan(_build_request(user))
    phase = out["status"]  # syllabus_pending | proposed | complete
    await goal_service.set_plan_phase(db, user_id, phase,
                                      pressure_score=out.get("pressure_score", 0),
                                      plan_feasibility=out.get("plan_feasibility", "feasible"))
    if phase == "proposed":
        await goal_service.store_planner_output(db, user_id, out)
    return out


@router.get("/{user_id}/plan")
async def get_plan_state(user_id: int, db: AsyncSession = Depends(get_db)):
    st = await goal_service.get_plan_state(db, user_id)
    goals = (await db.execute(select(Goal).where(Goal.user_id == user_id)
                               .order_by(Goal.level, Goal.recommended_date))).scalars().all()
    return {
        "phase": st.phase if st else "plan_generating",
        "monthly_goals": [_g_dict(g) for g in goals if g.level == "monthly"],
        "weekly_goals": [_g_dict(g) for g in goals if g.level == "weekly"],
        "daily_goals": [_g_dict(g) for g in goals if g.level == "daily"],
    }


@router.get("/{user_id}/today")
async def todays_flexible_goals(user_id: int, db: AsyncSession = Depends(get_db)):
    """Today's FLEXIBLE goals — not a fixed hourly timetable.

    The student completes them at any suitable time today. Missed work was
    already redistributed safely by the planner across ≥4 days."""
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    rows = (await db.execute(select(Goal).where(
        Goal.user_id == user_id, Goal.level == "daily",
        Goal.recommended_date >= week_start,
        Goal.status.in_(["proposed", "active", "in_progress", "rescheduled"]),
    ))).scalars().all()
    todays = [r for r in rows if r.recommended_date == today] or rows[:3]
    total = sum(int(r.estimated_minutes or 0) for r in todays)
    return {
        "date": today.isoformat(),
        "goals": [_g_dict(r) for r in todays],
        "total_estimated_minutes": total,
        "note": "Complete these at any suitable time today.",
    }


@router.post("/{user_id}/activate")
async def activate(user_id: int, db: AsyncSession = Depends(get_db)):
    """Server-side locking: monthly goals LOCK, started weeks LOCK."""
    await goal_service.activate_plan(db, user_id)
    await goal_service.set_plan_phase(db, user_id, "active")
    return {"ok": True}


@router.post("/{user_id}/check-in")
async def daily_check_in(user_id: int, payload: dict, db: AsyncSession = Depends(get_db)):
    """Record the short daily check-in and regenerate TODAY's flexible goals
    only (monthly/weekly scope untouched). Unsafe plans are reduced, never forced."""
    from app.models import User
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    out = plan(_build_request(user, check_in=payload))
    if out["status"] == "proposed":
        # Replace only *unlocked* daily goals for today; locked parents stay.
        stale = (await db.execute(select(Goal).where(
            Goal.user_id == user_id, Goal.level == "daily",
            Goal.lock_state == "unlocked",
            Goal.status.in_(["proposed", "active"])).with_for_update())).scalars().all()
        for g in stale:
            g.status = "superseded"
        await goal_service.store_planner_output(db, user_id, out)
    await db.commit()
    return out


def _g_dict(g: Goal) -> dict:
    return {
        "goal_id": g.goal_uid, "level": g.level, "title": g.title,
        "goal_type": g.goal_type, "status": g.status, "priority": g.priority,
        "syllabus": {"exam_code": g.exam_code, "syllabus_version_id": g.syllabus_version_id,
                     "syllabus_item_id": g.syllabus_item_id, "subject": g.subject,
                     "official_title": g.official_title},
        "estimated_minutes": g.estimated_minutes,
        "lock_state": g.lock_state, "flexibility": g.flexibility,
        "recommended_date": g.recommended_date.isoformat() if g.recommended_date else None,
        "week_start": g.week_start.isoformat() if g.week_start else None,
        "week_end": g.week_end.isoformat() if g.week_end else None,
        "explanation": g.explanation, "plan_version": g.plan_version,
    }
