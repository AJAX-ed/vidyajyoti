from fastapi import APIRouter
from datetime import datetime, timezone

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("", response_model=dict)
async def health_check():
    """
    Health check endpoint.
    Returns current UTC timestamp and status.
    """
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "db": "ok"  # Will be updated to check actual DB connection
    }
