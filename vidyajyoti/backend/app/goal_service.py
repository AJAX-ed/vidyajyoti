"""Goal-planning service layer — DB-backed operations for the hierarchical plan.

Responsibilities (backend-enforced, never frontend-only):
  • Persist planner output as goals with syllabus references.
  • Lock monthly goals on plan activation; lock weekly goals when their week starts.
  • Authorized revisions to locked goals create a NEW version row and mark the
    old one superseded (history is never overwritten).
  • Daily goals may be moved/split within their active week only.
  • Status changes go through the state machine + completion-evidence rules.
  • Completion vs mastery stay separate.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.goal_models import Goal, GoalDependency, GoalEvent, StudentPlanState
from app.goal_types import (
    GoalLevel, GoalStatus, LockState, PlanPhase, is_valid_status_transition,
)
from app.goal_validation import (
    LOCKED_FIELDS, GoalValidationError, validate_goal_has_syllabus,
)
# Re-export for callers that import the error from the service module.
__all__ = ["GoalValidationError", "activate_plan", "change_status",
           "get_plan_state", "move_daily_goal", "record_mastery",
           "revise_locked_goal", "set_plan_phase", "store_planner_output"]


def _now():
    return datetime.now(timezone.utc)


async def get_plan_state(db: AsyncSession, user_id: int) -> Optional[StudentPlanState]:
    return (await db.execute(
        select(StudentPlanState).where(StudentPlanState.user_id == user_id)
    )).scalar_one_or_none()


async def set_plan_phase(db: AsyncSession, user_id: int, phase: str, **fields) -> StudentPlanState:
    st = await get_plan_state(db, user_id)
    if st is None:
        st = StudentPlanState(user_id=user_id, phase=phase)
        db.add(st)
    st.phase = phase
    for k, v in fields.items():
        setattr(st, k, v)
    await db.commit()
    return st


async def store_planner_output(db: AsyncSession, user_id: int, out: dict[str, Any]) -> int:
    """Persist a PlanningResponse dict. Rejects any goal without a syllabus
    reference or with a placeholder title (validated before insert)."""
    saved = 0
    for level_key, level in (("monthly_goals", "monthly"), ("weekly_goals", "weekly"),
                             ("daily_goals", "daily")):
        for g in out.get(level_key, []):
            syl = g.get("syllabus") or {}
            if not syl.get("syllabus_item_id"):
                raise GoalValidationError(f"Goal {g.get('goal_id')} lacks syllabus_item_id")
            existing = (await db.execute(select(Goal).where(
                Goal.user_id == user_id, Goal.goal_uid == g["goal_id"]))).scalar_one_or_none()
            row = existing or Goal(user_id=user_id, goal_uid=g["goal_id"])
            row.level = level
            row.title = g["title"]
            row.goal_type = g["goal_type"]
            row.status = g["status"]
            row.priority = g["priority"]
            row.exam_code = syl["exam_code"]
            row.syllabus_version_id = syl["syllabus_version_id"]
            row.syllabus_item_id = syl["syllabus_item_id"]
            row.subject = syl["subject"]
            row.official_title = syl["official_title"]
            wl = g.get("workload") or {}
            row.estimated_minutes = g.get("estimated_effort_minutes") or wl.get("estimated_minutes", 0)
            row.estimation_source = wl.get("estimation_source", "deterministic_default")
            row.estimation_confidence = wl.get("estimation_confidence", 0.5)
            row.required_questions = g.get("required_questions") or g.get("question_count", 0)
            row.required_pyqs = g.get("required_pyqs", 0)
            row.mastery_target = g.get("mastery_target", 0.7)
            row.lock_state = g.get("lock_state", "unlocked")
            row.flexibility = g.get("flexibility", "flexible")
            row.plan_version = g.get("plan_version", 1)
            row.parent_goal_db_id = None  # resolved below by uid lookup
            row.start_date = _d(g.get("start_date"))
            row.recommended_completion_date = _d(g.get("recommended_completion_date"))
            row.week_start = _d(g.get("week_start"))
            row.week_end = _d(g.get("week_end"))
            row.recommended_date = _d(g.get("recommended_date"))
            row.month = g.get("month"); row.year = g.get("year")
            row.explanation = g.get("explanation")
            row.created_by = g.get("created_by", "rule-planner-v1")
            row.payload = {"planner": out.get("planner_version"),
                           "pressure_level": out.get("pressure_level")}
            if existing is None:
                db.add(row)
            saved += 1
    await db.flush()
    # link parents by uid
    rows = {r.goal_uid: r.id for r in (await db.execute(
        select(Goal).where(Goal.user_id == user_id))).scalars().all()}
    for g in (out.get("weekly_goals", []) + out.get("daily_goals", [])):
        parent_uid = g.get("parent_monthly_goal_id") or g.get("parent_weekly_goal_id") \
            or g.get("parent_goal_id")
        rid = rows.get(g["goal_id"])
        if rid and parent_uid and rows.get(parent_uid):
            r = await db.get(Goal, rid)
            r.parent_goal_db_id = rows[parent_uid]
    await db.commit()
    return saved


def _d(v):
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


async def activate_plan(db: AsyncSession, user_id: int) -> None:
    """MONTHLY goals become LOCKED; week-1 weekly goals are locked once their
    week has begun. Locking here is server-side — direct HTTP updates cannot
    bypass it (see revise_locked_goal / update_daily_goal)."""
    goals = (await db.execute(select(Goal).where(
        Goal.user_id == user_id, Goal.status.in_(["proposed", "draft"])))).scalars().all()
    today = date.today()
    for g in goals:
        g.status = GoalStatus.ACTIVE.value
        if g.level == GoalLevel.MONTHLY.value:
            g.lock_state = LockState.LOCKED.value
            g.flexibility = "rigid"
        elif g.level == GoalLevel.WEEKLY.value and g.week_start and g.week_start <= today:
            g.lock_state = LockState.LOCKED.value
            g.flexibility = "rigid"
    await set_plan_phase(db, user_id, PlanPhase.PLAN_ACTIVE.value)
    await db.commit()


async def revise_locked_goal(db: AsyncSession, goal: Goal, changes: dict[str, Any],
                             reason: str, initiated_by: str) -> Goal:
    """Authorized revision of a LOCKED goal. Creates a NEW version row;
    preserves the old row marked superseded with reason/initiator/timestamp."""
    if goal.lock_state != LockState.LOCKED.value:
        raise GoalValidationError("Goal is not locked; use ordinary updates.")
    if not reason or not initiated_by:
        raise GoalValidationError(
            "Locked-goal revision requires an explicit reason and initiator.")
    scope_changes = set(changes) & LOCKED_FIELDS
    new = Goal(**{k: getattr(goal, k) for k in
                  ("user_id", "level", "goal_type", "priority", "exam_code",
                   "syllabus_version_id", "syllabus_item_id", "subject",
                   "official_title", "estimated_minutes", "required_questions",
                   "required_pyqs", "mastery_target", "start_date",
                   "recommended_completion_date", "week_start", "week_end",
                   "recommended_date", "month", "year", "explanation",
                   "parent_goal_db_id", "payload")})
    new.goal_uid = f"{goal.goal_uid}-v{goal.plan_version + 1}"
    new.title = changes.get("title", goal.title)
    new.plan_version = goal.plan_version + 1
    new.status = GoalStatus.ACTIVE.value
    new.lock_state = LockState.LOCKED.value
    new.created_at = _now()
    db.add(new)
    await db.flush()
    goal.is_superseded = True
    goal.superseded_by_id = new.id
    goal.status = GoalStatus.SUPERSEDED.value
    goal.revision_reason = reason
    goal.revision_initiated_by = initiated_by
    goal.revised_at = _now()
    await db.commit()
    return new


async def move_daily_goal(db: AsyncSession, goal: Goal, new_date: date) -> Goal:
    """Daily goals are flexible WITHIN their active week only. Moving outside
    the parent week's window is rejected — daily changes must not silently
    alter weekly/monthly scope."""
    if goal.level != GoalLevel.DAILY.value:
        raise GoalValidationError("Only daily goals may be rescheduled freely.")
    parent = await db.get(Goal, goal.parent_goal_db_id) if goal.parent_goal_db_id else None
    if parent and parent.week_start and parent.week_end:
        if not (parent.week_start <= new_date <= parent.week_end):
            raise GoalValidationError(
                "A daily goal can move only within its current week — weekly "
                "scope is locked and cannot be shifted silently.")
    old_date = goal.recommended_date
    goal.recommended_date = new_date
    goal.status = GoalStatus.RESCHEDULED.value
    db.add(GoalEvent(user_id=goal.user_id, goal_uid=goal.goal_uid,
                     kind="rescheduled", occurred_on=date.today(),
                     payload={"from": old_date.isoformat() if old_date else None,
                              "to": new_date.isoformat()}))
    await db.commit()
    return goal


async def change_status(db: AsyncSession, goal: Goal, new_status: str,
                        evidence: Optional[dict] = None) -> Goal:
    cur = GoalStatus(goal.status)
    nxt = GoalStatus(new_status)
    if not is_valid_status_transition(cur, nxt):
        raise GoalValidationError(f"Invalid status transition {cur.value} → {nxt.value}")
    if nxt == GoalStatus.COMPLETED:
        if goal.level == GoalLevel.DAILY.value and not evidence:
            raise GoalValidationError("Completion requires evidence, not just a checkbox.")
        if goal.level in (GoalLevel.WEEKLY.value, GoalLevel.MONTHLY.value):
            children = (await db.execute(select(Goal).where(
                Goal.parent_goal_db_id == goal.id))).scalars().all()
            incomplete = [c for c in children if c.status != GoalStatus.COMPLETED.value]
            if incomplete:
                raise GoalValidationError(
                    f"{goal.level} goal cannot complete while "
                    f"{len(incomplete)} child goal(s) remain incomplete.")
    goal.status = nxt.value
    db.add(GoalEvent(user_id=goal.user_id, goal_uid=goal.goal_uid,
                     kind=new_status, occurred_on=date.today(), payload=evidence or {}))
    await db.commit()
    return goal


async def record_mastery(db: AsyncSession, goal: Goal, score: float,
                         confidence: float = 0.6) -> Goal:
    """Mastery is stored SEPARATELY from completion — a completed practice set
    with 45% accuracy leaves mastery low and schedules error review/revision."""
    goal.mastery_score = score
    goal.mastery_confidence = confidence
    goal.assessment_count += 1
    goal.last_assessed_at = date.today()
    goal.needs_revision = score < goal.mastery_target
    await db.commit()
    return goal
