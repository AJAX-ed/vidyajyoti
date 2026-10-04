from pydantic import BaseModel, ConfigDict
from typing import Any, Optional


class HealthResponse(BaseModel):
    status: str = "ok"
    time: str
    db: str = "ok"


class SubjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str


class ExamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    description: Optional[str] = None
    subjects: list[SubjectResponse] = []


class DayPlanSlot(BaseModel):
    start: int          # minutes since 00:00
    end: int
    type: str           # study|meal|school|routine|break|sleep|travel
    label: str


class OnboardingData(BaseModel):
    """Loose schema — the quiz evolves fast; validated as a dict."""
    model_config = ConfigDict(extra="allow")


class OnboardingSaveRequest(BaseModel):
    user_id: int
    data: dict[str, Any]
    plan: list[DayPlanSlot]


class OnboardingSaveResponse(BaseModel):
    ok: bool
    user_id: int
    slots_saved: int


class OnboardingGetResponse(BaseModel):
    onboarded: bool
    data: Optional[dict[str, Any]] = None
    plan: Optional[list[DayPlanSlot]] = None
