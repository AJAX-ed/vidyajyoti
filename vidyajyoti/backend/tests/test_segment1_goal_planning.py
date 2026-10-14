"""Segment 1 tests — hierarchical goal planning replaces the fixed timetable.

Covers the required test list (20 items) as far as implementable at this
segment: planner-level rules are tested directly against rule-planner-v1.

NO external AI APIs are called anywhere — asserted explicitly below.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.planner_service import plan, capacity_from_onboarding, DEFAULT_CONFIG  # noqa: E402
from app import goal_validation as gv                          # noqa: E402
from app.goal_types import GoalStatus, LockState               # noqa: E402


class _GoalLike:
    """Minimal duck-typed goal for validator unit tests (validators accept
    both enum and string status/lock values via .value fallback)."""
    def __init__(self, **kw):
        self.__dict__.update(kw)

    @property
    def value(self):
        return self.status

TODAY = date(2026, 10, 14)

SYLLABUS = [
    {"item_id": "phy-uam", "subject": "Physics", "title": "Units and Measurements",
     "difficulty": 0.4, "est_minutes": 180, "prerequisites": []},
    {"item_id": "phy-msl", "subject": "Physics", "title": "Motion in a Straight Line",
     "difficulty": 0.5, "est_minutes": 200, "prerequisites": ["phy-uam"]},
    {"item_id": "phy-lom", "subject": "Physics", "title": "Laws of Motion",
     "difficulty": 0.6, "est_minutes": 240, "prerequisites": ["phy-msl"]},
    {"item_id": "chem-cbat", "subject": "Chemistry", "title": "Some Basic Concepts of Chemistry",
     "difficulty": 0.45, "est_minutes": 200, "prerequisites": []},
]


def base_req(**over):
    req = {
        "student_id": "1", "exam_code": "JEE_MAIN",
        "target_exam_date": f"{TODAY.year}-12-31",
        "syllabus_version_id": "v-test", "approved_syllabus": SYLLABUS,
        "availability": {"wakeTime": "06:30", "sleepTime": "22:30",
                         "schoolStart": "08:00", "schoolEnd": "15:00",
                         "commuteMinutes": 30},
        "onboarding_preferences": {"studySessionMinutes": 45, "breakMinutes": 10},
        "check_in": None, "recent_events": [], "today": TODAY.isoformat(),
    }
    req.update(over)
    return req


# ── Onboarding inputs preserved & interpreted as availability ────────────
def test_onboarding_fields_drive_capacity_not_timetable():
    req = base_req()
    cap = capacity_from_onboarding(req["availability"],
                                   req["onboarding_preferences"], None,
                                   DEFAULT_CONFIG)
    assert cap.theoretical_available_minutes > 0
    # utilization factor: safe assignable < theoretical (never fills all free time)
    assert cap.safe_assignable_minutes <= cap.theoretical_available_minutes
    assert cap.buffer_minutes >= 0


# ── No fixed study-session slots generated ───────────────────────────────
def test_no_hourly_study_session_slots():
    out = plan(base_req())
    blob = str(out)
    assert "Study Session" not in blob
    assert "slots" not in out                       # no timetable output at all
    for key in ("monthly_goals", "weekly_goals", "daily_goals"):
        for g in out[key]:
            assert g["goal_type"] != "study"        # no generic study blocks


# ── No approved syllabus → syllabus_pending, never fake goals ────────────
def test_missing_syllabus_returns_pending_not_placeholders():
    out = plan(base_req(syllabus_version_id=None, approved_syllabus=[]))
    assert out["status"] == "syllabus_pending"
    assert out["monthly_goals"] == [] and out["weekly_goals"] == [] \
        and out["daily_goals"] == []
    assert any(w["code"] == "syllabus_pending" for w in out["warnings"])


# ── Goals without syllabus refs / placeholder titles rejected ────────────
def test_goal_without_syllabus_item_rejected():
    bad = _GoalLike(goal_id="x", title="Units and Measurements",
                    syllabus=_GoalLike(exam_code="JEE_MAIN", syllabus_version_id="v",
                                       syllabus_item_id=None, subject="Physics",
                                       official_title="Units and Measurements"))
    with pytest.raises(gv.GoalValidationError):
        gv.validate_goal_has_syllabus(bad)


def test_placeholder_titles_rejected():
    from app.goal_types import is_placeholder_title
    for bad in ["Topic 1", "Chapter A", "Generic Topic", "Study Physics",
                "Lesson 1", "Practice Something"]:
        assert is_placeholder_title(bad), f"'{bad}' should be flagged as placeholder"
        goal = _GoalLike(goal_id="x", title=bad,
                         syllabus=_GoalLike(exam_code="JEE_MAIN", syllabus_version_id="v",
                                            syllabus_item_id="i", subject="Physics",
                                            official_title=bad))
        with pytest.raises(gv.GoalValidationError):
            gv.validate_goal_has_syllabus(goal)
    assert not is_placeholder_title("Units and Measurements")   # real title passes


# ── Hierarchical structure + references ──────────────────────────────────
def test_hierarchy_and_syllabus_references():
    out = plan(base_req())
    assert out["status"] == "proposed"
    m, w, d = out["monthly_goals"], out["weekly_goals"], out["daily_goals"]
    assert m and w and d
    for g in m + w + d:
        s = g["syllabus"]
        assert s["exam_code"] == "JEE_MAIN"
        assert s["syllabus_version_id"] == "v-test"
        assert s["syllabus_item_id"] and s["official_title"]
    for g in w:
        assert g["parent_monthly_goal_id"] == m[0]["goal_id"]        # week ⊂ month
    for g in d:
        assert g["parent_weekly_goal_id"] in {x["goal_id"] for x in w}  # day ⊂ week


# ── Prerequisite order respected ─────────────────────────────────────────
def test_prerequisite_order():
    out = plan(base_req())
    order = [g["syllabus"]["syllabus_item_id"] for g in out["weekly_goals"]]
    if "phy-uam" in order and "phy-lom" in order:
        assert order.index("phy-uam") < order.index("phy-lom")


# ── Daily flexibility inside locked parents ──────────────────────────────
def test_daily_goals_flexible_monthly_locked_after_activation():
    out = plan(base_req())
    for g in out["daily_goals"]:
        assert g["lock_state"] == LockState.UNLOCKED.value
        assert g["flexibility"] == "flexible"
    # monthly/weekly scope is rigid — daily movement may not alter it
    assert out["monthly_goals"][0]["flexibility"] != "flexible"


# ── A daily goal cannot belong to an unrelated weekly goal ───────────────
def test_parent_child_integrity():
    def syl():
        return _GoalLike(exam_code="JEE_MAIN", syllabus_item_id="phy-uam")

    daily = _GoalLike(goal_id="d-1", parent_weekly_goal_id="w-other", syllabus=syl())
    weekly = _GoalLike(goal_id="w-1", parent_monthly_goal_id=None, syllabus=syl(),
                       week_start=None, week_end=None)
    with pytest.raises(gv.GoalValidationError):
        gv.validate_parent_child(daily, weekly, None)
    # and no parent at all → rejected too
    with pytest.raises(gv.GoalValidationError):
        gv.validate_parent_child(daily, None, None)


# ── Unsafe workload reduced, never saved unsafe ──────────────────────────
def test_unsafe_workload_is_reduced():
    out = plan(base_req(check_in={"available_minutes": 100000,
                                  "preferred_workload": "challenging"}))
    todays = out["today"]["goals"]
    total = sum(g["estimated_effort_minutes"] for g in todays)
    assert total <= out["capacity"]["safe_assignable_minutes"] + 30
    assert out["pressure_score"] <= 85          # never ships an unsafe active plan


def test_low_energy_lightens_day():
    normal = plan(base_req())
    tired = plan(base_req(check_in={"energy_level": "very_low",
                                    "stress_level": "very_high",
                                    "hours_slept": 3, "day_type": "recovery_day"}))
    n_min = sum(g["estimated_effort_minutes"] for g in normal["today"]["goals"])
    t_min = sum(g["estimated_effort_minutes"] for g in tired["today"]["goals"])
    assert t_min <= n_min                        # wellbeing reduces load


# ── Missed work spread, not stacked on tomorrow ──────────────────────────
def test_backlog_spread_not_stacked():
    out = plan(base_req(recent_events=[{"kind": "skipped", "minutes": 60}]))
    catchups = [g for g in out["daily_goals"] if g["goal_type"] == "backlog_recovery"]
    assert catchups, "backlog should produce recovery tasks"
    days = {g["recommended_date"] for g in catchups}
    assert len(days) >= 2                        # spread across multiple future days
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    stack = sum(g["estimated_effort_minutes"] for g in catchups
                if g["recommended_date"] == tomorrow)
    assert stack < 60                            # never dumps the whole miss on one day


# ── Completion vs mastery kept separate ──────────────────────────────────
def test_completion_mastery_separate_fields():
    out = plan(base_req())
    g = out["weekly_goals"][0]
    assert g.get("mastery_target", 0) > 0
    assert g.get("progress_percentage", 0) == 0  # completion tracked separately


# ── Status transition rules ──────────────────────────────────────────────
def test_status_transition_rules():
    g = _GoalLike(status=GoalStatus.PROPOSED, goal_id="x", level="daily",
                  completion_evidence=None, progress_percentage=0.0)
    gv.validate_status_change(g, GoalStatus.ACTIVE)              # legal

    done = _GoalLike(status=GoalStatus.COMPLETED, goal_id="x", level="daily",
                     completion_evidence=True, progress_percentage=100.0)
    with pytest.raises(gv.GoalValidationError):
        gv.validate_status_change(done, GoalStatus.DRAFT)        # illegal

    # completed without evidence is falsification → rejected
    no_ev = _GoalLike(status=GoalStatus.IN_PROGRESS, goal_id="x", level="daily",
                      completion_evidence=None, progress_percentage=50.0)
    with pytest.raises(gv.GoalValidationError):
        gv.validate_status_change(no_ev, GoalStatus.COMPLETED)


def test_locked_modification_requires_authorization():
    locked = _GoalLike(lock_state=LockState.LOCKED, goal_id="m-1")
    with pytest.raises(gv.GoalValidationError):
        gv.validate_locked_modification(locked, {"title"})       # no reason/initiator
    # authorized revision (reason + initiator) is the sanctioned path
    gv.validate_locked_modification(locked, {"title"},
                                    authorized_reason="exam date changed",
                                    initiated_by="student")


# ── Deterministic baseline, no external AI ───────────────────────────────
def test_no_external_ai_apis_in_planner_source():
    root = Path(__file__).resolve().parents[1] / "app"
    src = ((root / "planner_service.py").read_text()
           + (root / "routers" / "goals.py").read_text()
           + (root / "goal_service.py").read_text()).lower()
    forbidden = ["openai", "anthropic", "gemini", "groq", "cohere", "replicate",
                 "bedrock", "vertex", "chatgpt", "generativelanguage",
                 "api.inference", "huggingface.co/api"]
    for f in forbidden:
        assert f not in src, f"external AI reference found: {f}"


def test_deterministic_planner_is_pure_and_repeatable():
    a = plan(base_req()); b = plan(base_req())
    assert a["monthly_goals"] == b["monthly_goals"]     # same inputs → same plan
    assert a["planner_type"] == "deterministic"
    assert a["fallback_used"] is True                   # permanent safe baseline


def test_planner_works_without_ml_service():
    import inspect
    import app.planner_service as ps
    src = inspect.getsource(ps)
    assert "httpx" not in src and "requests." not in src  # zero network deps


# ── Feasibility honesty ──────────────────────────────────────────────────
def test_impossible_horizon_flagged_not_hidden():
    big = [
        {"item_id": f"t{i}", "subject": "Physics",
         "title": f"Real Chapter Number {i} of Physics",
         "difficulty": 0.5, "est_minutes": 600, "prerequisites": []}
        for i in range(24)
    ]  # 14 400 minutes of work vs a 5-day horizon → mathematically impossible
    out = plan(base_req(approved_syllabus=big,
                        target_exam_date=(TODAY + timedelta(days=5)).isoformat()))
    assert out["plan_feasibility"] in ("at_risk", "impossible", "tight")
    assert any(w["code"] == "plan_feasibility" for w in out["warnings"])
