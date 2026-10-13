"""Goal-planning validation rules (Segment 1) — backend-enforced.

These validators are called by the planner service and by API routes so that
rules can NEVER be bypassed from the frontend or via direct HTTP requests.

NO EXTERNAL AI APIs — pure deterministic Python rules.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from app.goal_types import (
    DailyGoal, GoalLevel, GoalStatus, LockState, MonthlyGoal, WeeklyGoal,
    is_placeholder_title, is_valid_status_transition,
)


class GoalValidationError(ValueError):
    """Raised when a goal violates a domain invariant."""


# ── Syllabus reference & placeholder rejection ────────────────────────

def validate_goal_has_syllabus(goal) -> None:
    """Every academic goal must reference a real syllabus item.

    Rejects goals missing exam_code / syllabus_version_id / syllabus_item_id,
    and rejects invented placeholder titles ("Topic 1", "Study Session 2"...).
    """
    syl = getattr(goal, "syllabus", None)
    if syl is None:
        raise GoalValidationError(
            f"Goal '{goal.goal_id}' has no syllabus reference — academic goals "
            "must point at an approved official syllabus item.")
    for field_name in ("exam_code", "syllabus_version_id", "syllabus_item_id",
                       "subject", "official_title"):
        if not getattr(syl, field_name, None):
            raise GoalValidationError(
                f"Goal '{goal.goal_id}' syllabus reference missing '{field_name}'.")
    if is_placeholder_title(goal.title):
        raise GoalValidationError(
            f"Goal title '{goal.title}' looks like a placeholder. Only official "
            "syllabus topic/chapter names may be used.")
    if is_placeholder_title(syl.official_title):
        raise GoalValidationError(
            f"Syllabus item title '{syl.official_title}' is a placeholder.")


# ── Hierarchy / parent-child integrity ────────────────────────────────

def validate_parent_child(daily: DailyGoal, weekly: Optional[WeeklyGoal],
                          monthly: Optional[MonthlyGoal]) -> None:
    """A daily goal may only belong to the weekly goal covering its date, and
    the weekly goal must belong to its parent monthly goal. Orphan or
    cross-linked goals are rejected (no unrelated parents)."""
    if weekly is None:
        raise GoalValidationError(
            f"Daily goal '{daily.goal_id}' has no parent weekly goal.")
    if daily.parent_weekly_goal_id != weekly.goal_id:
        raise GoalValidationError(
            f"Daily goal '{daily.goal_id}' references weekly goal "
            f"'{daily.parent_weekly_goal_id}' which is not its actual parent.")
    if daily.syllabus.exam_code != weekly.syllabus.exam_code:
        raise GoalValidationError("Daily/weekly goals span different exams.")
    if weekly.parent_monthly_goal_id:
        if monthly is None:
            raise GoalValidationError(
                f"Weekly goal '{weekly.goal_id}' references a monthly goal "
                "that does not exist.")
        if weekly.parent_monthly_goal_id != monthly.goal_id:
            raise GoalValidationError(
                f"Weekly goal '{weekly.goal_id}' parent mismatch.")
        # The week must lie inside the month's window
        if (weekly.week_start and monthly.start_date and
                weekly.week_end and monthly.recommended_completion_date):
            if (weekly.week_start < monthly.start_date or
                    weekly.week_end > _extend(monthly.recommended_completion_date)):
                raise GoalValidationError(
                    f"Weekly goal '{weekly.goal_id}' lies outside its parent "
                    "monthly window — daily rebalancing may not move work into "
                    "another month silently.")


def _extend(d: date, days: int = 10) -> date:
    return date.fromordinal(d.toordinal() + days)


# ── Status transitions ────────────────────────────────────────────────

def validate_status_change(goal, new_status: GoalStatus) -> None:
    if not is_valid_status_transition(goal.status, new_status):
        raise GoalValidationError(
            f"Invalid status transition {goal.status.value} → {new_status.value} "
            f"for goal '{goal.goal_id}'.")
    if new_status == GoalStatus.COMPLETED:
        ev = getattr(goal, "completion_evidence", None)
        if goal.level == GoalLevel.DAILY and not ev:
            raise GoalValidationError(
                f"Goal '{goal.goal_id}' cannot be marked completed without "
                "completion evidence.")
    # Parent completion never happens just because one child finished —
    # parent progress is recomputed from children, not set directly here.
    if goal.level in (GoalLevel.MONTHLY, GoalLevel.WEEKLY) and \
            new_status == GoalStatus.COMPLETED and goal.progress_percentage < 100.0:
        raise GoalValidationError(
            f"{goal.level.value} goal '{goal.goal_id}' cannot be completed while "
            f"progress is {goal.progress_percentage:.0f}% — completion requires "
            "all child goals + practice/quiz requirements satisfied.")


# ── Lock enforcement ──────────────────────────────────────────────────

LOCKED_FIELDS = {
    "title", "syllabus", "goal_type", "required_questions", "required_pyqs",
    "mastery_target", "week_start", "week_end", "start_date",
    "recommended_completion_date", "hard_deadline", "target_question_count",
    "pyq_target",
}


def validate_locked_modification(goal, changed_fields: Iterable[str],
                                 authorized_reason: Optional[str] = None,
                                 initiated_by: Optional[str] = None) -> None:
    """Locked goals (activated monthly / started weekly) may only change via an
    authorized revision carrying a reason + initiator. Ordinary daily planning
    and models must pass through here — enforced server-side, not in the UI."""
    changes = set(changed_fields)
    if not changes & LOCKED_FIELDS:
        return  # non-scope fields (e.g. flexible dates on dailies) are fine
    if goal.lock_state != LockState.LOCKED:
        return
    if not authorized_reason or not initiated_by:
        raise GoalValidationError(
            f"Goal '{goal.goal_id}' is LOCKED. Changing scope fields "
            f"{sorted(changes & LOCKED_FIELDS)} requires an explicit revision "
            "reason and initiator (student/admin), and must create a new "
            "version instead of overwriting history.")


# ── Workload safety ───────────────────────────────────────────────────

def validate_daily_workload(goals: list[DailyGoal], safe_assignable_minutes: int,
                            absolute_max_minutes: int) -> tuple[list[DailyGoal], list[str]]:
    """Greedy selection under pressure-aware ordering (see planner). Returns
    the accepted subset + human-readable notes about what was deferred."""
    total = sum(g.estimated_effort_minutes for g in goals)
    if total <= safe_assignable_minutes:
        return goals, []
    prio_rank = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    ordered = sorted(goals, key=lambda g: (prio_rank.get(g.priority.value, 2),
                                           -g.difficulty))
    kept: list[DailyGoal] = []
    running = 0
    deferred: list[str] = []
    for g in ordered:
        # Absolute max is a hard wall; safe limit yields priority-first fits.
        cap = absolute_max_minutes if g.priority.value == "critical" else safe_assignable_minutes
        if running + g.estimated_effort_minutes <= max(cap, safe_assignable_minutes) and running < absolute_max_minutes:
            kept.append(g)
            running += g.estimated_effort_minutes
        else:
            deferred.append(g.goal_id)
    notes = ([f"Today’s plan has been reduced based on your available time."]
             if deferred else [])
    if deferred:
        notes.append(f"{len(deferred)} task(s) were moved later in the week: "
                     + ", ".join(deferred))
    return kept, notes
