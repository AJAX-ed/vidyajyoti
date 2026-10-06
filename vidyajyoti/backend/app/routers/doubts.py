"""
Placeholder routers for future features.
These will be implemented with FastAPI + PostgreSQL backend
and self-hosted AI models (NO external AI APIs).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

# These routers are stubs for future implementation


# battlegrounds.py - Gamified quiz battles
battlegrounds_router = APIRouter(prefix="/api/battlegrounds", tags=["battlegrounds"])

@battlegrounds_router.get("")
async def get_battleground_sessions():
    """Get available battleground quiz sessions."""
    return {"message": "Battlegrounds module - Coming soon with FastAPI + PostgreSQL"}


# doubts.py - Student doubt/question system  
doubts_router = APIRouter(prefix="/api/doubts", tags=["doubts"])

@doubts_router.get("")
async def get_doubts():
    """Get user's doubts/questions."""
    return {"message": "Doubts module - Coming soon with FastAPI + PostgreSQL"}


# points.py - Points and rewards system
points_router = APIRouter(prefix="/api/points", tags=["points"])

@points_router.get("")
async def get_points():
    """Get user's points and coins."""
    return {"message": "Points module - Coming soon with FastAPI + PostgreSQL"}
