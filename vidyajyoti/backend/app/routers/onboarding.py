from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import User
from app.schemas import OnboardingSaveRequest, OnboardingSaveResponse, OnboardingGetResponse

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.post("/save", response_model=OnboardingSaveResponse)
async def save_onboarding(req: OnboardingSaveRequest, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, req.user_id)
    if user is None:
        # auto-create demo user so the flow works out of the box
        user = User(id=req.user_id, name="Student", role="student")
        db.add(user)
    user.onboarding_data = req.data
    user.day_plan = [slot.model_dump() for slot in req.plan]
    await db.commit()
    return OnboardingSaveResponse(ok=True, user_id=req.user_id, slots_saved=len(req.plan))


@router.get("/{user_id}", response_model=OnboardingGetResponse)
async def get_onboarding(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return OnboardingGetResponse(
        onboarded=user.day_plan is not None,
        data=user.onboarding_data,
        plan=user.day_plan,
    )
