from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import health, exams, onboarding

# Create FastAPI application
app = FastAPI(
    title="VidyaJyoti API",
    description="Backend API for VidyaJyoti - Indian Exam Preparation Platform",
    version="0.1.0"
)

# CORS middleware - allow frontend on port 3000 to access backend on port 8000
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include routers
app.include_router(health.router)
app.include_router(exams.router)
app.include_router(onboarding.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Welcome to VidyaJyoti API",
        "docs": "/docs",
        "redoc": "/redoc"
    }


# Note: Future routers (battlegrounds, doubts, points) will be added here
# along with self-hosted ML service integration for AI features.
# NO external AI APIs (OpenAI, Anthropic, Gemini, etc.) will be used.
# All AI functionality will be implemented via our own ML microservice.
