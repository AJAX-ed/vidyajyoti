from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel


# Health check response
class HealthResponse(BaseModel):
    status: str
    time: str
    db: str


# Exam schemas
class SubjectBase(BaseModel):
    name: str
    code: str


class SubjectResponse(SubjectBase):
    id: int
    exam_id: int

    class Config:
        from_attributes = True


class ExamBase(BaseModel):
    code: str
    name: str
    description: Optional[str] = None


class ExamResponse(ExamBase):
    id: int
    subjects: List[SubjectResponse] = []

    class Config:
        from_attributes = True


# Onboarding schemas
class DayPlanSlot(BaseModel):
    start: int  # minutes since 00:00
    end: int
    type: str  # study, meal, school, routine, break, sleep, travel
    label: str


class OnboardingData(BaseModel):
    education_type: str
    coaching_subtype: Optional[str] = None
    grade: str
    target_exams: List[str]
    morning_tasks: List[str]
    wake_time: str
    sleep_time: str
    leave_home: Optional[str] = None
    back_home: Optional[str] = None
    commute_minutes: Optional[int] = None
    coaching_start: Optional[str] = None
    coaching_end: Optional[str] = None
    breakfast_time: str
    lunch_time: str
    dinner_time: str
    meals_per_day: int
    peak_productivity: str
    study_session_minutes: int
    break_minutes: int


class OnboardingSaveRequest(BaseModel):
    user_id: int
    data: OnboardingData
    plan: List[DayPlanSlot]


class OnboardingSaveResponse(BaseModel):
    success: bool
    message: str


class OnboardingGetResponse(BaseModel):
    onboarded: bool
    data: Optional[OnboardingData] = None
    plan: Optional[List[DayPlanSlot]] = None


# Battleground schemas (for future implementation)
class BattlegroundSessionCreate(BaseModel):
    exam_id: int
    subject_id: int
    total_questions: int = 10
    duration_minutes: int = 30


class BattlegroundParticipantCreate(BaseModel):
    session_id: int
    user_id: int


# Doubt schemas
class DoubtCreate(BaseModel):
    user_id: int
    subject_id: Optional[int] = None
    title: str
    question_text: str


class DoubtResponse(BaseModel):
    id: int
    user_id: int
    subject_id: Optional[int]
    title: str
    question_text: str
    answer_text: Optional[str]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# Points schemas
class PointsTransactionCreate(BaseModel):
    user_id: int
    type: str
    delta_points: int = 0
    delta_coins: int = 0
    metadata: Optional[dict] = None


class PointsTransactionResponse(BaseModel):
    id: int
    user_id: int
    type: str
    delta_points: int
    delta_coins: int
    metadata: Optional[dict]
    created_at: datetime

    class Config:
        from_attributes = True
