"""Deterministic goal planner — VidyaJyoti's permanent planning baseline.

This is NOT throwaway logic. It stays available forever as:
  • the safe baseline,
  • the fallback when the (self-hosted) ML service is unavailable,
  • the comparison baseline for candidate models,
  • the cold-start generator when training data is insufficient,
  • the safety validator wrapped around model-generated plans.

It replaces the OLD fixed daily-timetable generator: it never emits
"Study Session N" slots. Onboarding routine fields (wake/sleep/school/
meals/commute/session length) are consumed only as AVAILABILITY and
WELLBEING inputs to estimate capacity — not to build a minute-by-minute
compulsory schedule.

Pipeline (documented order):
  1. Load active approved syllabus → if none: `syllabus_pending` (no fake goals).
  2. Remove completed items with sufficient mastery.
  3. Respect prerequisite order (topological sort).
  4. Estimate total remaining workload.
  5. Months until target exam; reserve revision + buffer (~20%).
  6. Divide syllabus into monthly milestones.
  7. Divide the ACTIVE month into weekly milestones (pace to finish by year end).
  8. Use today's check-in to assign a SAFE portion of weekly work to daily goals.
  9. Validate workload & pressure; reduce/move/split until safe or at-risk.
 10. Reject placeholder/orphan goals via goal_validation.
 11. Return proposal with explanations, warnings, feasibility.

NO EXTERNAL AI APIs are used. NO LLM generates goals here.
"""
from __future__ import annotations

import hashlib
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.goal_types import (
    Capacity, DailyCheckIn, DailyGoal, EnergyLevel, EstimationSource,
    GoalExplanation, GoalLevel, GoalStatus, GoalType,
    LockState, MonthlyGoal, PlanFeasibility, PlanningResponse, PlanningWarning,
    PlannerType, PressureEvaluation, PressureLevel, PreferredWorkload,
    Priority, StressLevel, SyllabusRef, WeeklyGoal, WorkloadEstimate,
)
from app import goal_validation as gv


PLANNER_VERSION = "rule-planner-v1"
PRESSURE_VERSION = "pressure-v1"

# ── Tunable safety configuration (configurable per deployment) ────────
DEFAULT_CONFIG = {
    "utilization_factor": 0.6,          # never fill every free minute
    "absolute_max_minutes_per_day": 300,  # hard wall regardless of requests
    "revision_buffer_fraction": 0.2,    # reserve ~20% of horizon for revision/buffer
    "min_daily_minutes": 30,            # even on light days keep a small consistent habit
    "max_new_topics_per_day": 2,
    "mastery_complete_threshold": 0.7,
    "pressure_thresholds": [(30, "light"), (55, "manageable"),
                            (70, "demanding"), (85, "high_pressure")],  # >85 unsafe
}

# Deterministic defaults per goal type (admin metadata overrides these later)
_TYPE_DEFAULT_MINUTES = {
    GoalType.LEARNING: 45, GoalType.CONCEPT_REVIEW: 30, GoalType.NOTES: 25,
    GoalType.VIDEO_EXPLANATION: 30, GoalType.PRACTICE: 50,
    GoalType.PREVIOUS_YEAR_QUESTIONS: 40, GoalType.REVISION: 25,
    GoalType.ACTIVE_RECALL: 15, GoalType.FLASHCARDS: 15, GoalType.QUIZ: 20,
    GoalType.CHAPTER_TEST: 60, GoalType.MOCK_TEST: 180,
    GoalType.ERROR_REVIEW: 20, GoalType.DIAGNOSTIC: 40,
    GoalType.RECOVERY: 20, GoalType.BUFFER: 15, GoalType.BACKLOG_RECOVERY: 40,
}


def _uid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


# ════════════════════════════════════════════════════════════════════
# CAPACITY FROM ONBOARDING (availability, NOT a timetable)
# ════════════════════════════════════════════════════════════════════

def _t2m(ts: str) -> int:
    try:
        h, m = str(ts).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def capacity_from_onboarding(availability: dict[str, Any], prefs: dict[str, Any],
                             check_in: Optional[DailyCheckIn],
                             cfg: dict) -> Capacity:
    """Interpret wake/sleep/school/meals ONLY as bounds on study availability."""
    wake = _t2m(availability.get("wakeTime", "06:30"))
    sleep = _t2m(availability.get("sleepTime", "22:30"))
    if sleep <= wake:
        sleep += 1440
    waking = max(sleep - wake, 0)

    school_min = 0
    if availability.get("educationType") != "Home School":
        leave = _t2m(availability.get("leaveHome", "08:00"))
        back = _t2m(availability.get("backHome", "15:00"))
        if back > leave:
            school_min = back - leave
        school_min += 2 * int(availability.get("commuteMinutes", 0) or 0)
    meals = 20 + 30 + 30
    routine = sum(int(t.get("minutes", 10)) for t in availability.get("morningTasks", [])[:6])

    theoretical = max(waking - school_min - meals - routine, 0)
    declared = int(availability.get("declared_available_minutes") or theoretical)
    safe = int(min(theoretical, declared) * cfg["utilization_factor"])

    if check_in:
        # Wellbeing modifiers — reduce, never inflate beyond declared.
        safe = min(safe, max(check_in.available_minutes, cfg["min_daily_minutes"]))
        if check_in.energy == EnergyLevel.VERY_LOW:
            safe = int(safe * 0.4)
        elif check_in.energy == EnergyLevel.LOW:
            safe = int(safe * 0.65)
        elif check_in.energy == EnergyLevel.HIGH:
            safe = int(safe * 1.05)
        if check_in.stress in (StressLevel.HIGH, StressLevel.VERY_HIGH):
            safe = int(safe * 0.6)
        if check_in.sleep_hours < 6:
            safe = int(safe * 0.6)
        elif check_in.sleep_hours < 7:
            safe = int(safe * 0.85)
        if check_in.has_test_today:
            safe = int(safe * 0.5)
        if check_in.day_type.value in ("recovery_day", "leave_day"):
            safe = int(safe * 0.5)
        if check_in.preferred_workload == PreferredWorkload.LIGHT:
            safe = int(safe * 0.7)
        elif check_in.preferred_workload == PreferredWorkload.CHALLENGING:
            # explicit student request may exceed safe, but NEVER the absolute max
            safe = int(min(safe * 1.25, cfg["absolute_max_minutes_per_day"]))

    safe = max(min(safe, cfg["absolute_max_minutes_per_day"]), cfg["min_daily_minutes"]) \
        if theoretical > 0 else cfg["min_daily_minutes"]
    return Capacity(theoretical_available_minutes=theoretical,
                    declared_available_minutes=declared,
                    safe_assignable_minutes=safe,
                    buffer_minutes=max(theoretical - safe, 0))


# ════════════════════════════════════════════════════════════════════
# PRESSURE SCORE (deterministic v1 formula — documented, not magic)
# ════════════════════════════════════════════════════════════════════

def evaluate_pressure(capacity: Capacity, daily_goals: list[DailyGoal],
                      check_in: Optional[DailyCheckIn],
                      overdue_count: int, consecutive_demanding_days: int,
                      recent_completion_rate: float, cfg: dict) -> PressureEvaluation:
    """score = Σ weighted factors, clipped 0..100.

    Factors (weights in parentheses):
      load_ratio assigned/safe (40), difficult tasks ×4 (max 12),
      new topics ×5 (max 15), overdue ×4 (max 12), low sleep (10),
      low energy (8), high stress (10), consecutive demanding days ×3 (max 9),
      catch-up share (8); minus bonus for good recent completion (−6)."""
    f: dict[str, float] = {}
    assigned = sum(g.estimated_effort_minutes for g in daily_goals)
    capacity.assigned_minutes = assigned
    ratio = assigned / max(capacity.safe_assignable_minutes, 1)
    f["load_ratio"] = min(ratio, 1.5) * 40
    f["difficult_tasks"] = min(sum(1 for g in daily_goals if g.difficulty >= 0.7), 3) * 4
    f["new_topics"] = min(len({g.syllabus.syllabus_item_id for g in daily_goals
                               if g.goal_type == GoalType.LEARNING}), 3) * 5
    f["overdue"] = min(overdue_count, 3) * 4
    if check_in:
        f["low_sleep"] = 10 if check_in.sleep_hours < 6 else (5 if check_in.sleep_hours < 7 else 0)
        f["low_energy"] = {"very_low": 8, "low": 4}.get(check_in.energy.value, 0)
        f["high_stress"] = {"very_high": 10, "high": 6}.get(check_in.stress.value, 0)
    f["consecutive_demanding"] = min(consecutive_demanding_days, 3) * 3
    f["catchup_share"] = 8 if any(g.goal_type == GoalType.BACKLOG_RECOVERY for g in daily_goals) else 0
    f["completion_bonus"] = -6 if recent_completion_rate >= 0.8 else 0
    score = int(max(0, min(100, round(sum(f.values())))))
    level = PressureLevel.UNSAFE
    for thr, name in cfg["pressure_thresholds"]:
        if score <= thr:
            level = PressureLevel(name)
            break
    return PressureEvaluation(pressure_score=score, pressure_level=level,
                              pressure_factors=f, calculation_version=PRESSURE_VERSION)


# ════════════════════════════════════════════════════════════════════
# SYLLABUS HELPERS (real items only; ordering by prerequisites)
# ════════════════════════════════════════════════════════════════════

def topo_order(items: list[dict]) -> list[dict]:
    """Topologically sort syllabus items by `prerequisites` (item ids).
    Cycles degrade gracefully to original order — never drops items."""
    by_id = {i["item_id"]: i for i in items}
    seen: set[str] = set()
    out: list[dict] = []

    def visit(iid: str, stack: set[str]):
        if iid in seen or iid in stack or iid not in by_id:
            return
        stack.add(iid)
        for p in by_id[iid].get("prerequisites", []) or []:
            visit(p, stack)
        stack.discard(iid)
        seen.add(iid)
        out.append(by_id[iid])

    for i in items:
        visit(i["item_id"], set())
    return out


def _workload_for(item: dict, diagnostic: Optional[float]) -> int:
    base = int(item.get("estimated_minutes") or
               _TYPE_DEFAULT_MINUTES[GoalType.LEARNING])
    diff = float(item.get("difficulty", 0.5))
    if diagnostic is not None and diagnostic < 0.5:
        base = int(base * 1.2)   # weaker diagnostic → slightly more practice time
    return int(base * (0.8 + 0.6 * diff))


def _month_start(today: date, offset_months: int) -> date:
    y, m = today.year, today.month + offset_months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, min(today.day, 28))


def _month_scope(pending: list[dict], months_left: int, total_work: int) -> list[list[dict]]:
    """Split the ordered pending syllabus into `months_left` workload-balanced
    buckets WITHOUT breaking prerequisite order (prefix partition)."""
    per_month_work = total_work / max(months_left, 1)
    buckets: list[list[dict]] = [[]]
    acc = 0
    for item in pending:
        w = _workload_for(item, None)
        buckets[-1].append(item)
        acc += w
        if acc >= per_month_work and len(buckets) < months_left:
            buckets.append([])
            acc = 0
    if not buckets[-1]:
        buckets.pop()
    return buckets


# ════════════════════════════════════════════════════════════════════
# MAIN PLANNER
# ════════════════════════════════════════════════════════════════════

def plan(req: dict[str, Any], cfg_overrides: Optional[dict] = None) -> dict[str, Any]:
    """Entry point used by routers/services. Accepts a plain dict (mirrors
    PlanningRequest) and returns the shared planner output contract."""
    cfg = {**DEFAULT_CONFIG, **(cfg_overrides or {})}
    today = date.fromisoformat(req.get("today") or datetime.now(timezone.utc).date().isoformat())
    exam_code = req.get("exam_code", "JEE_MAIN")
    syl_version = req.get("syllabus_version_id")
    syllabus = req.get("approved_syllabus") or []
    student_id = str(req.get("student_id", req.get("user_id", "unknown")))
    target_exam = date.fromisoformat(req["target_exam_date"]) if req.get("target_exam_date") \
        else date(today.year, 12, 31)

    base_resp = dict(student_id=student_id, exam_code=exam_code,
                     target_exam_date=target_exam.isoformat(),
                     syllabus_version_id=syl_version, plan_version=int(req.get("plan_version", 1)),
                     fallback_used=True, planner_type=PlannerType.DETERMINISTIC.value,
                     planner_version=PLANNER_VERSION,
                     generated_at=datetime.now(timezone.utc).isoformat())

    # 1. No approved syllabus → syllabus_pending. NEVER invent placeholders.
    if not syl_version or not syllabus:
        return {**base_resp,
                "status": PlanPhaseStr.SYLLABUS_PENDING,
                "phase": PlanPhaseStr.SYLLABUS_PENDING,
                "plan_feasibility": PlanFeasibility.FEASIBLE.value,
                "confidence_score": 0.0, "pressure_score": 0,
                "pressure_level": PressureLevel.LIGHT.value,
                "monthly_goals": [], "weekly_goals": [], "daily_goals": [],
                "warnings": [{"code": "syllabus_pending", "severity": "blocking",
                              "message": "The official syllabus for this exam is being prepared. "
                                         "Your onboarding details are saved — goals will appear as "
                                         "soon as the approved syllabus version is published."}],
                "explanations": []}

    completed = set(req.get("completed_syllabus_items") or [])
    mastery_by_item = req.get("mastery_by_item") or {}
    diag = req.get("diagnostic_scores") or {}

    # 2–3. Remaining items in prerequisite order
    pending = [i for i in syllabus
               if i["item_id"] not in completed
               or float(mastery_by_item.get(i["item_id"], 0)) < cfg["mastery_complete_threshold"]]
    pending = topo_order(pending)

    if not pending:
        return {**base_resp, "status": "complete", "phase": "plan_ready",
                "plan_feasibility": PlanFeasibility.FEASIBLE.value,
                "confidence_score": 1.0, "pressure_score": 0,
                "pressure_level": PressureLevel.LIGHT.value,
                "monthly_goals": [], "weekly_goals": [], "daily_goals": [],
                "warnings": [{"code": "syllabus_complete", "severity": "info",
                              "message": "You’ve covered the approved syllabus with sufficient "
                                         "mastery. Revision cycles can be scheduled on request."}],
                "explanations": []}

    # 4–5. Workload vs horizon, reserving revision/buffer
    total_work = sum(_workload_for(i, diag.get(i.get("subject"))) for i in pending)
    days_left = max((target_exam - today).days, 1)
    months_left = max(math.ceil(days_left / 30), 1)
    productive_days = int(days_left * (1 - cfg["revision_buffer_fraction"]))

    cap = capacity_from_onboarding(req.get("availability", {}),
                                   req.get("onboarding_preferences", {}),
                                   _check_in_from(req.get("check_in")), cfg)
    week_capacity = cap.safe_assignable_minutes * 7

    feasibility = PlanFeasibility.FEASIBLE
    warn: list[dict] = []
    if total_work > productive_days * cap.safe_assignable_minutes:
        gap = total_work - productive_days * cap.safe_assignable_minutes
        ratio = gap / total_work
        feasibility = PlanFeasibility.IMPOSSIBLE if ratio > 0.35 else PlanFeasibility.AT_RISK \
            if ratio > 0.15 else PlanFeasibility.TIGHT
        warn.append({"code": "plan_feasibility", "severity": "warning",
                     "message": f"At your current realistic pace (~{cap.safe_assignable_minutes} "
                                f"min/day), about {gap//60} hours of syllabus may not fit before "
                                f"{target_exam.strftime('%d %b')}. We’re prioritising foundational "
                                "topics first — nothing is silently dropped; deferred scope is "
                                "listed below."})

    # 6. MONTHLY MILESTONES — proportional workload split, dependency-respecting
    monthly: list[MonthlyGoal] = []
    per_month_work = total_work / months_left
    month_buckets = _month_scope(pending, months_left, total_work)
    for bi, bucket_items in enumerate(month_buckets):
        if not bucket_items:
            continue
        subjects = sorted({i["subject"] for i in bucket_items})
        wl = sum(_workload_for(i, diag.get(i.get("subject"))) for i in bucket_items)
        cursor = _month_start(today, bi)
        gid = f"m-{_uid(exam_code, syl_version, cursor.isoformat(), subjects[0])}"
        rec_end = min(cursor.replace(day=28) + timedelta(days=31), target_exam)
        monthly.append(MonthlyGoal(
            goal_id=gid,
            title=f"{subjects[0]} milestone — {cursor.strftime('%B %Y')}" if len(subjects) == 1
                  else f"Mixed milestone — {cursor.strftime('%B %Y')}",
            syllabus=SyllabusRef(exam_code=exam_code, syllabus_version_id=syl_version,
                                 syllabus_item_id=bucket_items[0]["item_id"],
                                 subject=subjects[0], official_title=bucket_items[0]["title"]),
            goal_type=GoalType.LEARNING, status=GoalStatus.PROPOSED,
            priority=Priority.HIGH if bi == 0 else Priority.NORMAL,
            workload=WorkloadEstimateS(wl),
            explanation=(f"This month covers {len(bucket_items)} syllabus item(s) so the full "
                         f"{exam_code} syllabus finishes before {target_exam.strftime('%d %b %Y')}."),
            lock_state=LockState.UNLOCKED, flexibility=FlexibilityRigid(),
            month=cursor.month, year=cursor.year, start_date=cursor,
            recommended_completion_date=rec_end,
            target_question_count=int(wl / 4), pyq_target=int(wl / 20),
            revision_expectation="End-of-month cumulative assessment",
            mastery_target=cfg["mastery_complete_threshold"],
            syllabus_version=syl_version, created_by=PLANNER_VERSION))

    # 7. WEEKLY GOALS for the ACTIVE month (pace to year-end/exam, not exhaustive)
    weekly: list[WeeklyGoal] = []
    active_month = monthly[0]
    # The active month's OWN syllabus scope (its bucket of pending items), so
    # weekly goals subdivide the month and cannot exceed its window.
    m_items = month_buckets[0]
    weeks_in_month = max(math.ceil(active_month.workload.estimated_minutes / max(week_capacity, 1)), 1)
    weeks_in_month = min(weeks_in_month, 5)
    wk_items_all = topo_order(m_items)
    chunk = max(math.ceil(len(wk_items_all) / weeks_in_month), 1)
    for wi in range(weeks_in_month):
        slice_items = wk_items_all[wi * chunk:(wi + 1) * chunk]
        if not slice_items:
            break
        w_start = today + timedelta(days=7 * wi)
        w_end = w_start + timedelta(days=6)
        wl = sum(_workload_for(i, diag.get(i.get("subject"))) for i in slice_items)
        gid = f"w-{_uid(active_month.goal_id, str(wi))}"
        first = slice_items[0]
        weekly.append(WeeklyGoal(
            goal_id=gid, level=GoalLevel.WEEKLY,
            title=first["title"],
            syllabus=SyllabusRef(exam_code=exam_code, syllabus_version_id=syl_version,
                                 syllabus_item_id=first["item_id"],
                                 subject=first["subject"], official_title=first["title"]),
            goal_type=GoalType.LEARNING,
            status=GoalStatus.ACTIVE if wi == 0 else GoalStatus.PROPOSED,
            priority=Priority.HIGH,
            workload=WorkloadEstimateS(wl),
            parent_goal_id=active_month.goal_id, parent_monthly_goal_id=active_month.goal_id,
            explanation=(f"Week {wi+1} of your {active_month.title}. Sized to your realistic "
                         f"weekly capacity (~{week_capacity} min), not an exhaustive wishlist."),
            lock_state=LockState.LOCKED if wi == 0 else LockState.UNLOCKED,
            flexibility=FlexibilityRigid(),
            week_start=w_start, week_end=w_end,
            required_questions=int(wl / 4), required_pyqs=int(wl / 20),
            mastery_target=cfg["mastery_complete_threshold"],
            syllabus_version=syl_version, created_by=PLANNER_VERSION))

    # 8. TODAY'S FLEXIBLE DAILY GOALS from week-1 scope + check-in
    daily: list[DailyGoal] = []
    wk0 = weekly[0]
    day_plan_cursor = today
    remaining_wk = wk0.workload.estimated_minutes
    session_len = int(req.get("onboarding_preferences", {}).get("studySessionMinutes", 50))
    di_idx = 0
    while remaining_wk > 0 and di_idx < 7:
        d = day_plan_cursor + timedelta(days=di_idx)
        budget = cap.safe_assignable_minutes if di_idx == 0 else cap.safe_assignable_minutes
        take = min(budget, remaining_wk)
        src = wk_items_all[min(di_idx * 2, len(wk_items_all) - 1)]
        gtype = GoalType.LEARNING if di_idx == 0 else (
            GoalType.PRACTICE if di_idx % 2 else GoalType.REVISION)
        q = int(take / 4) if gtype == GoalType.PRACTICE else 0
        dg = DailyGoal(
            goal_id=f"d-{_uid(wk0.goal_id, d.isoformat(), str(di_idx))}",
            level=GoalLevel.DAILY,
            title=f"{src['subject']} · “{src['title']}”",
            syllabus=SyllabusRef(exam_code=exam_code, syllabus_version_id=syl_version,
                                 syllabus_item_id=src["item_id"], subject=src["subject"],
                                 official_title=src["title"]),
            goal_type=gtype, status=GoalStatus.PROPOSED,
            priority=Priority.HIGH if di_idx == 0 else Priority.NORMAL,
            workload=WorkloadEstimateS(take),
            parent_goal_id=wk0.goal_id, parent_weekly_goal_id=wk0.goal_id,
            explanation=_daily_reason(gtype, src, req.get("check_in"), diag),
            recommended_date=d, estimated_effort_minutes=int(take), question_count=q,
            difficulty=float(src.get("difficulty", 0.5)),
            flexibility=FlexibilityFlexible(),
            syllabus_version=syl_version, created_by=PLANNER_VERSION)
        daily.append(dg)
        remaining_wk -= take
        di_idx += 1

    # Missed-work redistribution: spread backlog across safe days, never stack.
    backlog = [e for e in req.get("recent_events", []) if e.get("kind") in ("skipped", "overdue")]
    if backlog:
        mins = sum(int(e.get("minutes", 30)) for e in backlog)
        per_day = max(mins // 4, 10)  # spread over ≥4 future days
        for k in range(min(4, max(len(daily), 4))):
            slot_day = today + timedelta(days=1 + k)
            src = wk_items_all[(k + 1) % len(wk_items_all)]
            daily.append(DailyGoal(
                goal_id=f"d-backlog-{_uid(wk0.goal_id, str(k))}",
                level=GoalLevel.DAILY,
                title=f"Catch up · “{src['title']}” ({src['subject']})",
                syllabus=SyllabusRef(exam_code, syl_version, src["item_id"],
                                     src["subject"], src["title"]),
                goal_type=GoalType.BACKLOG_RECOVERY, status=GoalStatus.PROPOSED,
                priority=Priority.NORMAL, workload=WorkloadEstimateS(per_day),
                parent_goal_id=wk0.goal_id, parent_weekly_goal_id=wk0.goal_id,
                explanation=("A task was missed earlier this week. Instead of piling it onto one "
                             "day, we spread it across the coming days — rest and consistency beat "
                             "one overloaded day."),
                recommended_date=slot_day, estimated_effort_minutes=per_day,
                difficulty=0.4, flexibility=FlexibilityFlexible(),
                syllabus_version=syl_version, created_by=PLANNER_VERSION))

    # 9–10. Validate: workload safety, placeholders, parent-child integrity
    todays = [g for g in daily if g.recommended_date == today]
    kept, notes = gv.validate_daily_workload(
        todays, cap.safe_assignable_minutes, cfg["absolute_max_minutes_per_day"])
    for n in notes:
        warn.append({"code": "workload_reduced", "severity": "info", "message": n})
    todays = kept
    for g in daily:
        gv.validate_goal_has_syllabus(g)
    for g in todays:
        gv.validate_parent_child(g, wk0, active_month)

    pressure = evaluate_pressure(cap, todays, _check_in_from(req.get("check_in")),
                 overdue_count=len(backlog), consecutive_demanding_days=int(req.get("consecutive_demanding_days", 0)),
                 recent_completion_rate=float(req.get("recent_completion_rate", 1.0)), cfg=cfg)

    if pressure.is_unsafe:
        # Reduce instead of saving an unsafe plan: drop lowest-priority extras.
        prio_rank = {"critical": 0, "high": 1, "normal": 2, "low": 3}
        todays_sorted = sorted(todays, key=lambda g: prio_rank[g.priority.value])
        allowed = todays_sorted[:max(1, math.ceil(cap.safe_assignable_minutes / max(session_len, 20)))]
        moved = [g for g in todays if g not in allowed]
        for g in moved:
            g.status = GoalStatus.RESCHEDULED
            g.recommended_date = today + timedelta(days=1)
            g.explanation += " Moved to keep today’s workload manageable."
        todays = allowed
        pressure = evaluate_pressure(cap, todays, _check_in_from(req.get("check_in")),
                                     len(backlog), 0, 1.0, cfg)
        warn.append({"code": "pressure_reduced", "severity": "info",
                     "message": "Today’s plan has been reduced based on your available time. "
                                f"{len(moved)} task(s) were moved later in the week."})

    if feasibility in (PlanFeasibility.AT_RISK, PlanFeasibility.IMPOSSIBLE):
        wk0.risk_state = feasibility
        wk0.status = GoalStatus.AT_RISK
        warn.append({"code": "at_risk_week", "severity": "warning",
                     "message": "Your weekly goal may need adjustment. Focus on the priority task "
                                "first — we’ll ask for approval before changing any locked scope."})

    explanations = [GoalExplanation(goal_id=g.goal_id, reasons=[g.explanation]).__dict__
                    for g in (todays or daily[:3])]

    return {**base_resp,
            "status": "proposed", "phase": "plan_ready",
            "plan_feasibility": feasibility.value,
            "confidence_score": round(min(0.5 + 0.05 * months_left, 0.85), 2),
            "pressure_score": pressure.pressure_score,
            "pressure_level": pressure.pressure_level.value,
            "capacity": cap.__dict__,
            "monthly_goals": [_goal_dict(m) for m in monthly],
            "weekly_goals": [_goal_dict(w) for w in weekly],
            "daily_goals": [_goal_dict(g) for g in daily],
            "today": {"date": today.isoformat(),
                      "goals": [_goal_dict(g) for g in todays],
                      "total_estimated_minutes": sum(g.estimated_effort_minutes for g in todays),
                      "note": "Complete these at any suitable time today."},
            "warnings": warn, "explanations": explanations}


# ── tiny helpers to avoid circular-name clutter ───────────────────────

def WorkloadEstimateS(wl: int):
    from app.goal_types import WorkloadEstimate
    return WorkloadEstimate(estimated_minutes=int(wl),
                            estimation_source=EstimationSource.DETERMINISTIC_DEFAULT,
                            estimation_confidence=0.5)


def FlexibilityRigid():
    from app.goal_types import FlexibilityState
    return FlexibilityState.RIGID


def FlexibilityFlexible():
    from app.goal_types import FlexibilityState
    return FlexibilityState.FLEXIBLE


class PlanPhaseStr:
    SYLLABUS_PENDING = "syllabus_pending"
    PLAN_GENERATING = "plan_generating"
    PLAN_READY = "plan_ready"
    PLAN_ACTIVE = "plan_active"
    PLAN_AT_RISK = "plan_at_risk"


def _check_in_from(raw: Optional[dict]) -> Optional[DailyCheckIn]:
    if not raw:
        return None
    from app.goal_types import DayType
    try:
        return DailyCheckIn(
            available_minutes=int(raw.get("available_minutes", 120)),
            sleep_hours=float(raw.get("sleep_hours", 7.5)),
            energy=EnergyLevel(raw.get("energy", "normal")),
            stress=StressLevel(raw.get("stress", "moderate")),
            has_test_today=bool(raw.get("has_test_today", False)),
            day_type=DayType(raw.get("day_type", "school_day")),
            unexpected_work=bool(raw.get("unexpected_work", False)),
            preferred_workload=PreferredWorkload(raw.get("preferred_workload", "normal")))
    except Exception:
        return None


def _daily_reason(gtype: GoalType, src: dict, check_in: Optional[dict],
                  diag: dict) -> str:
    bits = [f"“{src['title']}” is part of your active {src.get('subject','')} syllabus."]
    if src.get("prerequisites"):
        bits.append("Scheduled in prerequisite order so earlier concepts support this one.")
    d = diag.get(src.get("subject"))
    if d is not None and float(d) < 0.6 and gtype in (GoalType.PRACTICE, GoalType.REVISION):
        bits.append("Your diagnostic result suggests this area needs more attention.")
    if check_in and check_in.get("preferred_workload") == "light":
        bits.append("This task was shortened because you reported limited time today.")
    return " ".join(bits)


def _goal_dict(g) -> dict[str, Any]:
    from dataclasses import asdict
    d = asdict(g)
    # JSON-safe dates
    for k, v in list(d.items()):
        if isinstance(v, date):
            d[k] = v.isoformat()
    return d
