"""VidyaJyoti goal-planning domain types (Segment 1).

Hierarchical goal system that REPLACES the old fixed daily timetable:

    STUDENT EXAM PLAN
    └── MONTHLY GOALS   (locked after activation — stable syllabus milestones)
        └── WEEKLY GOALS (locked when the week begins)
            └── DAILY GOALS (flexible — move/split/reorder within the week)
                └── TASKS
                    └── COMPLETION / PERFORMANCE EVENTS

This module is pure stdlib (no DB, no HTTP, no AI) so it can be unit-tested
in isolation and reused by services, routers, and the safety validator.

ABSOLUTE RULE enforced here: every academic goal must reference a real
syllabus item (exam_code + syllabus_version_id + syllabus_item_id).
Placeholder titles ("Topic 1", "Study Session 2", "Chapter A", ...) are
rejected outright. When no approved syllabus exists the planner returns
`syllabus_pending` — it never invents topics.

NO EXTERNAL AI APIs are used anywhere in this package.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Optional


# ════════════════════════════════════════════════════════════════════
# DOMAIN ENUMS
# ════════════════════════════════════════════════════════════════════

class GoalLevel(str, Enum):
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"
    TASK = "task"


class GoalType(str, Enum):
    """Closed vocabulary — arbitrary strings are rejected at validation."""
    LEARNING = "learning"
    CONCEPT_REVIEW = "concept_review"
    NOTES = "notes"
    VIDEO_EXPLANATION = "video_explanation"
    PRACTICE = "practice"
    PREVIOUS_YEAR_QUESTIONS = "previous_year_questions"
    REVISION = "revision"
    ACTIVE_RECALL = "active_recall"
    FLASHCARDS = "flashcards"
    QUIZ = "quiz"
    CHAPTER_TEST = "chapter_test"
    MOCK_TEST = "mock_test"
    ERROR_REVIEW = "error_review"
    DIAGNOSTIC = "diagnostic"
    RECOVERY = "recovery"
    BUFFER = "buffer"
    BACKLOG_RECOVERY = "backlog_recovery"


class GoalStatus(str, Enum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    ACTIVE = "active"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    SKIPPED = "skipped"
    OVERDUE = "overdue"
    AT_RISK = "at_risk"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class PressureLevel(str, Enum):
    LIGHT = "light"             # 0–30
    MANAGEABLE = "manageable"   # 31–55
    DEMANDING = "demanding"     # 56–70
    HIGH_PRESSURE = "high_pressure"  # 71–85
    UNSAFE = "unsafe"           # 86–100


class LockState(str, Enum):
    UNLOCKED = "unlocked"
    LOCKED = "locked"
    REVISION_PENDING = "revision_pending"  # change requested, awaiting approval


class FlexibilityState(str, Enum):
    FLEXIBLE = "flexible"          # daily goals & tasks
    SEMI_RIGID = "semi_rigid"      # weekly before lock
    RIGID = "rigid"                # locked weekly/monthly scope


class PlannerType(str, Enum):
    DETERMINISTIC = "deterministic"   # rule-based baseline (always available)
    LOCAL_ML = "local_ml"             # future self-hosted trained models
    HYBRID = "hybrid"                 # ML proposal validated by rules


class PlanFeasibility(str, Enum):
    FEASIBLE = "feasible"
    TIGHT = "tight"               # fits, but with little slack
    AT_RISK = "at_risk"           # cannot fit safely without revision
    IMPOSSIBLE = "impossible"


class DependencyType(str, Enum):
    PREREQUISITE = "prerequisite"
    RECOMMENDED_BEFORE = "recommended_before"
    ASSESSMENT_AFTER = "assessment_after"
    REVISION_AFTER = "revision_after"
    UNLOCK_AFTER_COMPLETION = "unlock_after_completion"
    UNLOCK_AFTER_MASTERY = "unlock_after_mastery"


class EstimationSource(str, Enum):
    ADMIN = "admin"
    DETERMINISTIC_DEFAULT = "deterministic_default"
    CONTENT_METADATA = "content_metadata"
    HISTORICAL_AVERAGE = "historical_average"
    STUDENT_HISTORY = "student_history"
    LOCAL_MODEL = "local_model"


class DayType(str, Enum):
    SCHOOL_DAY = "school_day"
    COACHING_DAY = "coaching_day"
    SCHOOL_AND_COACHING = "school_and_coaching"
    HOLIDAY = "holiday"
    WEEKEND = "weekend"
    LEAVE_DAY = "leave_day"
    EXAM_DAY = "exam_day"
    RECOVERY_DAY = "recovery_day"


class EnergyLevel(str, Enum):
    VERY_LOW = "very_low"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    VERY_HIGH = "very_high"


class StressLevel(str, Enum):
    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class PreferredWorkload(str, Enum):
    LIGHT = "light"
    NORMAL = "normal"
    CHALLENGING = "challenging"


class PlanPhase(str, Enum):
    """Temporary onboarding states replacing the old 'day plan generated' step."""
    SYLLABUS_PENDING = "syllabus_pending"   # no approved syllabus yet
    PLAN_GENERATING = "plan_generating"     # planner running asynchronously
    PLAN_READY = "plan_ready"               # proposed plan available
    PLAN_ACTIVE = "plan_active"             # student activated the plan
    PLAN_AT_RISK = "plan_at_risk"           # remaining work cannot fit safely


# ════════════════════════════════════════════════════════════════════
# STATUS TRANSITION TABLE (backend-enforced state machine)
# ════════════════════════════════════════════════════════════════════

VALID_STATUS_TRANSITIONS: dict[GoalStatus, set[GoalStatus]] = {
    GoalStatus.DRAFT: {GoalStatus.PROPOSED, GoalStatus.CANCELLED},
    GoalStatus.PROPOSED: {GoalStatus.ACTIVE, GoalStatus.DRAFT, GoalStatus.CANCELLED,
                          GoalStatus.SUPERSEDED},
    GoalStatus.ACTIVE: {GoalStatus.IN_PROGRESS, GoalStatus.SKIPPED, GoalStatus.OVERDUE,
                        GoalStatus.RESCHEDULED, GoalStatus.AT_RISK, GoalStatus.CANCELLED,
                        GoalStatus.COMPLETED, GoalStatus.PARTIALLY_COMPLETED},
    GoalStatus.IN_PROGRESS: {GoalStatus.COMPLETED, GoalStatus.PARTIALLY_COMPLETED,
                             GoalStatus.SKIPPED, GoalStatus.OVERDUE, GoalStatus.RESCHEDULED},
    GoalStatus.PARTIALLY_COMPLETED: {GoalStatus.IN_PROGRESS, GoalStatus.COMPLETED,
                                     GoalStatus.OVERDUE, GoalStatus.RESCHEDULED},
    GoalStatus.SKIPPED: {GoalStatus.RESCHEDULED, GoalStatus.ACTIVE, GoalStatus.AT_RISK},
    GoalStatus.OVERDUE: {GoalStatus.IN_PROGRESS, GoalStatus.RESCHEDULED, GoalStatus.COMPLETED,
                         GoalStatus.PARTIALLY_COMPLETED, GoalStatus.CANCELLED, GoalStatus.AT_RISK},
    GoalStatus.AT_RISK: {GoalStatus.ACTIVE, GoalStatus.IN_PROGRESS, GoalStatus.RESCHEDULED,
                         GoalStatus.COMPLETED, GoalStatus.CANCELLED},
    GoalStatus.RESCHEDULED: {GoalStatus.ACTIVE, GoalStatus.IN_PROGRESS, GoalStatus.SKIPPED,
                             GoalStatus.OVERDUE, GoalStatus.CANCELLED},
    GoalStatus.COMPLETED: set(),   # terminal — completion needs evidence, never undone silently
    GoalStatus.CANCELLED: {GoalStatus.SUPERSEDED},
    GoalStatus.SUPERSEDED: set(),  # terminal
}


def is_valid_status_transition(current: GoalStatus, new: GoalStatus) -> bool:
    return new in VALID_STATUS_TRANSITIONS.get(current, set())


# ════════════════════════════════════════════════════════════════════
# PLACEHOLDER-TITLE REJECTION
# ════════════════════════════════════════════════════════════════════

_PLACEHOLDER_PATTERNS = [
    r"^topic\s*[\dA-Z]+$",
    r"^chapter\s+[A-Z0-9]$",
    r"^generic\s+topic$",
    r"^study\s+session\s*\d+$",
    r"^subject\s+task$",
    r"^lesson\s+\d+$",
    r"^practice\s+something$",
    r"^complete\s+(one\s+)?(a\s+)?chapter$",
    r"^study\s+(physics|chemistry|maths?|biology)$",
    r"^misc.*$",
    r"^untitled.*$",
    r"^tbd.*$",
]
_PLACEHOLDER_RE = re.compile("|".join(_PLACEHOLDER_PATTERNS), re.IGNORECASE)


def is_placeholder_title(title: str) -> bool:
    """True if the title looks like an invented placeholder rather than a real
    official syllabus topic/chapter name."""
    if not title or not title.strip():
        return True
    return bool(_PLACEHOLDER_RE.match(title.strip()))


# ════════════════════════════════════════════════════════════════════
# TYPED STRUCTURES
# ════════════════════════════════════════════════════════════════════

@dataclass
class SyllabusRef:
    """Every academic goal MUST carry one of these. Goals without a real
    syllabus reference are rejected by the validator."""
    exam_code: str
    syllabus_version_id: str
    syllabus_item_id: str
    subject: str
    official_title: str


@dataclass
class WorkloadEstimate:
    estimated_minutes: int
    estimation_source: EstimationSource = EstimationSource.DETERMINISTIC_DEFAULT
    estimation_confidence: float = 0.5          # 0..1
    minimum_minutes: int = 0
    maximum_minutes: int = 0
    actual_minutes: Optional[int] = None        # filled from events
    pages: int = 0
    videos: int = 0
    questions: int = 0
    pyqs: int = 0
    quizzes: int = 0
    mock_tests: int = 0
    revision_reps: int = 0

    def __post_init__(self):
        if self.minimum_minutes == 0 and self.maximum_minutes == 0:
            # conservative ±20% band around the estimate
            self.minimum_minutes = int(self.estimated_minutes * 0.8)
            self.maximum_minutes = int(self.estimated_minutes * 1.2)


@dataclass
class GoalDependency:
    dependency_type: DependencyType
    from_goal_id: str
    to_goal_id: Optional[str] = None
    to_syllabus_item_id: Optional[str] = None
    required_mastery: Optional[float] = None    # e.g. unlock after mastery ≥ 0.7
    diagnostic_key: Optional[str] = None


@dataclass
class MasteryState:
    """Kept SEPARATE from completion: a task can be completed with low mastery."""
    mastery_score: float = 0.0                  # 0..1
    mastery_confidence: float = 0.0
    last_assessed_at: Optional[date] = None
    assessment_count: int = 0
    needs_revision: bool = False


@dataclass
class BaseGoal:
    goal_id: str
    title: str                                  # must be syllabus-derived
    syllabus: SyllabusRef
    goal_type: GoalType
    level: GoalLevel = GoalLevel.TASK
    status: GoalStatus = GoalStatus.DRAFT
    priority: Priority = Priority.NORMAL
    workload: WorkloadEstimate = field(default_factory=lambda: WorkloadEstimate(0))
    parent_goal_id: Optional[str] = None
    explanation: str = ""                       # student-friendly reason
    lock_state: LockState = LockState.UNLOCKED
    flexibility: FlexibilityState = FlexibilityState.FLEXIBLE
    progress_percentage: float = 0.0
    mastery: MasteryState = field(default_factory=MasteryState)
    plan_version: int = 1
    syllabus_version: str = ""
    created_by: str = "rule-planner-v1"         # planner version or model name
    superseded_by: Optional[str] = None


@dataclass
class MonthlyGoal(BaseGoal):
    month: int = 1
    year: int = 2026
    start_date: Optional[date] = None
    recommended_completion_date: Optional[date] = None
    hard_deadline: Optional[date] = None
    target_question_count: int = 0
    pyq_target: int = 0
    revision_expectation: str = ""
    assessment_expectation: str = ""
    mastery_target: float = 0.7
    flex_override_reason: Optional[str] = None  # required for authorized revision

    def __post_init__(self):
        self.level = GoalLevel.MONTHLY


@dataclass
class WeeklyGoal(BaseGoal):
    parent_monthly_goal_id: Optional[str] = None
    week_start: Optional[date] = None
    week_end: Optional[date] = None
    risk_state: PlanFeasibility = PlanFeasibility.FEASIBLE
    required_questions: int = 0
    required_pyqs: int = 0
    mastery_target: float = 0.7

    def __post_init__(self):
        self.level = GoalLevel.WEEKLY


@dataclass
class DailyGoal(BaseGoal):
    parent_weekly_goal_id: Optional[str] = None
    recommended_date: Optional[date] = None
    estimated_effort_minutes: int = 0
    question_count: int = 0
    difficulty: float = 0.5                     # 0..1 normalized topic difficulty
    pressure_contribution: int = 0              # points added to day's pressure score
    completion_evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.level = GoalLevel.DAILY


@dataclass
class Task(BaseGoal):
    parent_daily_goal_id: Optional[str] = None

    def __post_init__(self):
        self.level = GoalLevel.TASK


@dataclass
class GoalEvent:
    """Records what the student actually did. Completion is evidence-driven."""
    event_id: str
    goal_id: str
    kind: str          # started | completed | skipped | rescheduled |
                       # partially_completed | too_difficult | insufficient_time
    occurred_at: Optional[date] = None
    payload: dict[str, Any] = field(default_factory=dict)


# ════════════════════════════════════════════════════════════════════
# CHECK-IN / CAPACITY / PRESSURE
# ════════════════════════════════════════════════════════════════════

@dataclass
class DailyCheckIn:
    available_minutes: int
    sleep_hours: float
    energy: EnergyLevel
    stress: StressLevel
    has_test_today: bool
    day_type: DayType
    unexpected_work: bool
    preferred_workload: PreferredWorkload


@dataclass
class Capacity:
    theoretical_available_minutes: int
    declared_available_minutes: int
    safe_assignable_minutes: int     # what the planner may allocate against
    assigned_minutes: int = 0
    buffer_minutes: int = 0


@dataclass
class PressureEvaluation:
    pressure_score: int                      # 0..100
    pressure_level: PressureLevel
    pressure_factors: dict[str, float] = field(default_factory=dict)
    calculation_version: str = "pressure-v1"

    @property
    def is_unsafe(self) -> bool:
        return self.pressure_level == PressureLevel.UNSAFE


@dataclass
class PlanningWarning:
    code: str            # e.g. "workload_overflow", "at_risk_week", "syllabus_pending"
    message: str         # supportive, non-shaming language
    goal_id: Optional[str] = None
    severity: str = "info"   # info | warning | blocking


@dataclass
class GoalExplanation:
    goal_id: str
    audience: str = "student"     # only "student"-safe text; never expose other students' data
    reasons: list[str] = field(default_factory=list)


@dataclass
class PlanningRequest:
    student_id: str
    exam_code: str
    target_exam_date: date
    syllabus_version_id: Optional[str] = None
    approved_syllabus: Optional[list[dict[str, Any]]] = None  # items w/ ids/titles/prereqs
    completed_syllabus_items: list[str] = field(default_factory=list)
    mastery_by_item: dict[str, float] = field(default_factory=dict)
    diagnostic_scores: dict[str, float] = field(default_factory=dict)
    availability: dict[str, Any] = field(default_factory=dict)       # from onboarding
    onboarding_preferences: dict[str, Any] = field(default_factory=dict)
    current_monthly_goals: list[MonthlyGoal] = field(default_factory=list)
    current_weekly_goals: list[WeeklyGoal] = field(default_factory=list)
    recent_events: list[GoalEvent] = field(default_factory=list)
    check_in: Optional[DailyCheckIn] = None
    workload_limits: dict[str, int] = field(default_factory=dict)


@dataclass
class PlanningResponse:
    student_id: str
    exam_code: str
    target_exam_date: str
    syllabus_version_id: Optional[str]
    plan_version: int
    status: str                                   # draft|proposed|active...
    plan_feasibility: PlanFeasibility
    fallback_used: bool
    planner_type: PlannerType
    planner_version: str
    confidence_score: float
    pressure_score: int
    pressure_level: PressureLevel
    monthly_goals: list[dict[str, Any]]
    weekly_goals: list[dict[str, Any]]
    daily_goals: list[dict[str, Any]]
    warnings: list[PlanningWarning]
    explanations: list[GoalExplanation]
    generated_at: str
    phase: PlanPhase = PlanPhase.PLAN_READY
