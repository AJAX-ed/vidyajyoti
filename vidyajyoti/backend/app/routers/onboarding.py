from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from ..database import get_db
from ..models import User
from ..schemas import (
    OnboardingSaveRequest,
    OnboardingSaveResponse,
    OnboardingGetResponse,
    OnboardingData,
    DayPlanSlot,
)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


@router.post("/save", response_model=OnboardingSaveResponse)
async def save_onboarding(
    request: OnboardingSaveRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Save onboarding data and day plan for a user.
    
    This endpoint stores:
    - The quiz answers (education type, schedule, preferences, etc.)
    - The generated day plan (timeline of slots)
    
    In production, this would create or update the User record.
    For now, it returns a success response.
    """
    try:
        # Check if user exists
        result = await db.execute(select(User).where(User.id == request.user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            # Create new user
            user = User(
                id=request.user_id,
                name=f"User_{request.user_id}",
                role="student"
            )
            db.add(user)
        
        # Convert Pydantic models to dict for JSON storage
        onboarding_data_dict = request.data.model_dump()
        day_plan_list = [slot.model_dump() for slot in request.plan]
        
        # Update user's onboarding data and day plan
        user.onboarding_data = onboarding_data_dict
        user.day_plan = day_plan_list
        
        await db.commit()
        await db.refresh(user)
        
        return OnboardingSaveResponse(
            success=True,
            message="Onboarding data saved successfully"
        )
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{user_id}", response_model=OnboardingGetResponse)
async def get_onboarding(
    user_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Get onboarding status and data for a user.
    
    Returns:
    - onboarded: boolean indicating if user completed onboarding
    - data: the quiz answers (if onboarded)
    - plan: the generated day plan (if onboarded)
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        return OnboardingGetResponse(
            onboarded=False,
            data=None,
            plan=None
        )
    
    if user.onboarding_data is None:
        return OnboardingGetResponse(
            onboarded=False,
            data=None,
            plan=None
        )
    
    # Parse stored data back to Pydantic models
    try:
        onboarding_data = OnboardingData(**user.onboarding_data)
        day_plan = [DayPlanSlot(**slot) for slot in user.day_plan] if user.day_plan else []
    except Exception:
        # If parsing fails, return raw data
        return OnboardingGetResponse(
            onboarded=True,
            data=user.onboarding_data,  # type: ignore
            plan=user.day_plan  # type: ignore
        )
    
    return OnboardingGetResponse(
        onboarded=True,
        data=onboarding_data,
        plan=day_plan
    )
