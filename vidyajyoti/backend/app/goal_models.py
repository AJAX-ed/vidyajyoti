"""Goal-planning ORM models (Segment 1 foundation; full schema/migrations in Segment 3).

Design rules encoded here:
  • Every academic goal row carries syllabus references (exam_code,
    syllabus_version_id, syllabus_item_id) — placeholder goals cannot be stored.
  • Locking is a DB column enforced by the service layer, not the frontend.
  • Revisions NEVER overwrite history: an edit to a locked goal creates a new
    row (new plan_version) and marks the old row `superseded` with reason,
    initiator and timestamp.
  • Completion evidence and mastery are stored separately from status.
"""
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class Goal(Base):
    """One row per goal at any level of the hierarchy (monthly/weekly/daily/task)."""
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True)
    goal_uid = Column(String(64), index=True)          # planner-generated id
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)

    level = Column(String(10), nullable=False)         # monthly|weekly|daily|task
    parent_goal_db_id = Column(Integer, ForeignKey("goals.id"), nullable=True)
    title = Column(String(255), nullable=False)        # official syllabus name only
    goal_type = Column(String(40), nullable=False)     # closed GoalType vocabulary
    status = Column(String(30), nullable=False, default="draft")
    priority = Column(String(10), nullable=False, default="normal")

    # ── mandatory syllabus reference (validated before insert) ──────────
    exam_code = Column(String(50), nullable=False)
    syllabus_version_id = Column(String(64), nullable=False)
    syllabus_item_id = Column(String(64), nullable=False)
    subject = Column(String(120), nullable=False)
    official_title = Column(String(255), nullable=False)

    # ── workload & progress ─────────────────────────────────────────────
    estimated_minutes = Column(Integer, default=0)
    estimation_source = Column(String(30), default="deterministic_default")
    estimation_confidence = Column(Float, default=0.5)
    actual_minutes = Column(Integer, nullable=True)
    required_questions = Column(Integer, default=0)
    required_pyqs = Column(Integer, default=0)
    progress_percentage = Column(Float, default=0.0)

    # ── mastery kept SEPARATE from completion ───────────────────────────
    mastery_score = Column(Float, default=0.0)
    mastery_confidence = Column(Float, default=0.0)
    mastery_target = Column(Float, default=0.7)
    last_assessed_at = Column(Date, nullable=True)
    assessment_count = Column(Integer, default=0)
    needs_revision = Column(Boolean, default=False)

    # ── locking & versioning (backend-enforced) ─────────────────────────
    lock_state = Column(String(20), default="unlocked")   # unlocked|locked|revision_pending
    flexibility = Column(String(12), default="flexible")
    plan_version = Column(Integer, default=1)
    is_superseded = Column(Boolean, default=False)
    superseded_by_id = Column(Integer, ForeignKey("goals.id"), nullable=True)
    revision_reason = Column(Text, nullable=True)
    revision_initiated_by = Column(String(40), nullable=True)   # student|admin|system
    revised_at = Column(DateTime(timezone=True), nullable=True)

    # ── scheduling windows ──────────────────────────────────────────────
    month = Column(Integer, nullable=True)
    year = Column(Integer, nullable=True)
    start_date = Column(Date, nullable=True)
    recommended_completion_date = Column(Date, nullable=True)
    hard_deadline = Column(Date, nullable=True)
    week_start = Column(Date, nullable=True)
    week_end = Column(Date, nullable=True)
    recommended_date = Column(Date, nullable=True, index=True)   # daily goals

    explanation = Column(Text, nullable=True)                     # student-friendly reason
    payload = Column(JSONB, nullable=True, default=dict)          # extra structured fields
    created_by = Column(String(60), default="rule-planner-v1")    # planner/model version
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GoalDependency(Base):
    """Prerequisite graph by identifiers (never title comparison)."""
    __tablename__ = "goal_dependencies"
    id = Column(Integer, primary_key=True)
    from_goal_uid = Column(String(64), nullable=False, index=True)
    to_goal_uid = Column(String(64), nullable=True)
    to_syllabus_item_id = Column(String(64), nullable=True)
    dependency_type = Column(String(30), nullable=False)
    required_mastery = Column(Float, nullable=True)


class GoalEvent(Base):
    """Evidence stream: started/completed/skipped/rescheduled/partially_completed/
    too_difficult/insufficient_time. Completion requires events, not checkboxes."""
    __tablename__ = "goal_events"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    goal_uid = Column(String(64), index=True, nullable=False)
    kind = Column(String(30), nullable=False)
    occurred_on = Column(Date, nullable=False, index=True)
    payload = Column(JSONB, nullable=True, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DailyCheckInRecord(Base):
    """Wellbeing check-in collected BEFORE daily goals are generated.
    Influences DAILY goals only — never silently modifies weekly/monthly scope."""
    __tablename__ = "daily_check_ins"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    day = Column(Date, nullable=False, index=True)
    available_minutes = Column(Integer, default=120)
    sleep_hours = Column(Float, default=7.5)
    energy = Column(String(12), default="normal")
    stress = Column(String(12), default="moderate")
    has_test_today = Column(Boolean, default=False)
    day_type = Column(String(24), default="school_day")
    unexpected_work = Column(Boolean, default=False)
    preferred_workload = Column(String(12), default="normal")
    pressure_score = Column(Integer, nullable=True)
    pressure_level = Column(String(16), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class StudentPlanState(Base):
    """Per-student planning phase replacing the old 'day_plan generated' flag."""
    __tablename__ = "student_plan_states"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, index=True)
    phase = Column(String(24), default="syllabus_pending")  # syllabus_pending|plan_generating|plan_ready|plan_active|plan_at_risk
    exam_code = Column(String(50), nullable=True)
    target_exam_date = Column(Date, nullable=True)
    syllabus_version_id = Column(String(64), nullable=True)
    plan_version = Column(Integer, default=1)
    plan_feasibility = Column(String(16), nullable=True)
    last_planned_on = Column(Date, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
