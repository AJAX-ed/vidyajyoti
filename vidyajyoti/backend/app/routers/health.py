from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.schemas import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)):
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "unreachable"
    return HealthResponse(status="ok", time=datetime.now(timezone.utc).isoformat(), db=db_status)
