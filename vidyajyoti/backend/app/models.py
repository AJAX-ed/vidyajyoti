from sqlalchemy import Column, Integer, String, Date, Text, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(255), unique=True, nullable=True)
    role = Column(String(30), default="student")  # student | admin
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    onboarding_data = Column(JSONB, nullable=True)
    day_plan = Column(JSONB, nullable=True)
    # Weekly/monthly syllabus goals computed by the self-hosted ML planner.
    weekly_goals = Column(JSONB, nullable=True)
    monthly_goals = Column(JSONB, nullable=True)


class DailyPlan(Base):
    """One generated timetable per user per calendar day.

    This is what makes every day different: when a student logs in on a new
    day, the backend asks the ML service for that date's rotation of the
    stored weekly goals and persists the resulting plan here (keyed by
    (user_id, plan_date))."""
    __tablename__ = "daily_plans"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    plan_date = Column(Date, index=True)
    slots = Column(JSONB, nullable=False, default=list)     # timeline blocks
    focus_topics = Column(JSONB, nullable=True, default=list)  # topics today
    week_index = Column(Integer, nullable=True)            # which goal week
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Exam(Base):
    __tablename__ = "exams"
    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True)   # e.g. JEE_MAIN
    name = Column(String(120))
    description = Column(Text, nullable=True)


class Subject(Base):
    __tablename__ = "subjects"
    id = Column(Integer, primary_key=True)
    exam_id = Column(Integer, ForeignKey("exams.id"))
    name = Column(String(120))
    code = Column(String(50))


class BattlegroundSession(Base):
    __tablename__ = "battleground_sessions"
    id = Column(Integer, primary_key=True)
    exam_id = Column(Integer, ForeignKey("exams.id"))
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    total_questions = Column(Integer, default=10)
    duration_minutes = Column(Integer, default=15)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BattlegroundParticipant(Base):
    __tablename__ = "battleground_participants"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("battleground_sessions.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    score = Column(Integer, default=0)
    coins_earned = Column(Integer, default=0)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class Doubt(Base):
    __tablename__ = "doubts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    title = Column(String(255))
    question_text = Column(Text)
    answer_text = Column(Text, nullable=True)
    status = Column(String(20), default="open")  # open | answered


class PointsTransaction(Base):
    __tablename__ = "points_transactions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    type = Column(String(40))  # battle_win | doubt_answered | daily_plan ...
    delta_points = Column(Integer, default=0)
    delta_coins = Column(Integer, default=0)
    # 'metadata' is a reserved attribute name on SQLAlchemy declarative classes,
    # so the Python attribute is meta_info while the DB column stays 'metadata'.
    meta_info = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
