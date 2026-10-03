"""
VidyaJyoti ML Service - FastAPI Application

This service provides self-hosted AI/ML endpoints for the main VidyaJyoti backend.
NO external AI APIs are used. All models run locally.

Endpoints:
- POST /api/ml/recommend-next-topic - Get topic recommendations
- POST /api/ml/schedule-adjust - Adjust study schedule
- POST /api/ml/answer-doubt - Answer student doubts (RAG-based)
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from .models_ml import (
    get_topic_recommender,
    get_schedule_optimizer,
    get_rag_tutor,
    initialize_all_models
)

# Create FastAPI application
app = FastAPI(
    title="VidyaJyoti ML Service",
    description="Self-hosted ML/AI service for VidyaJyoti platform",
    version="0.1.0"
)

# CORS middleware - allow main backend to access ML service
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request/Response schemas
class TopicRecommendationRequest(BaseModel):
    user_id: int
    exam_id: int
    past_performance: Dict[str, float]
    studied_topics: List[int]


class TopicRecommendationResponse(BaseModel):
    recommendations: List[Dict[str, Any]]


class ScheduleAdjustRequest(BaseModel):
    current_plan: List[Dict[str, Any]]
    performance_history: List[Dict[str, Any]]
    user_preferences: Dict[str, Any]


class ScheduleAdjustResponse(BaseModel):
    adjusted_plan: List[Dict[str, Any]]


class DoubtAnswerRequest(BaseModel):
    user_id: int
    subject_id: int
    question_text: str
    top_k: int = 5


class DoubtAnswerResponse(BaseModel):
    answer: str
    confidence: float
    sources: List[Dict[str, Any]]
    subject_id: int


# Startup event - initialize models
@app.on_event("startup")
async def startup_event():
    """Initialize ML models on startup."""
    import os
    model_path = os.getenv("MODEL_PATH", "./models")
    # Note: In production, this would load actual models
    # For now, we just prepare the infrastructure
    print(f"ML Service starting up... (model path: {model_path})")
    # initialize_all_models(model_path)


# Endpoints
@app.get("/")
async def root():
    """Root endpoint for ML service."""
    return {
        "message": "VidyaJyoti ML Service - Self-Hosted AI",
        "endpoints": [
            "/api/ml/recommend-next-topic",
            "/api/ml/schedule-adjust",
            "/api/ml/answer-doubt"
        ]
    }


@app.post("/api/ml/recommend-next-topic", response_model=TopicRecommendationResponse)
async def recommend_next_topic(request: TopicRecommendationRequest):
    """
    Recommend next topics to study based on user performance.
    
    Uses a self-hosted recommendation model (scikit-learn or PyTorch).
    NO external AI APIs.
    """
    try:
        recommender = get_topic_recommender()
        recommendations = recommender.recommend(
            user_id=request.user_id,
            exam_id=request.exam_id,
            past_performance=request.past_performance,
            studied_topics=request.studied_topics
        )
        return TopicRecommendationResponse(recommendations=recommendations)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ml/schedule-adjust", response_model=ScheduleAdjustResponse)
async def adjust_schedule(request: ScheduleAdjustRequest):
    """
    Adjust study schedule based on performance and preferences.
    
    Uses optimization algorithms and possibly a small neural network.
    NO external AI APIs.
    """
    try:
        optimizer = get_schedule_optimizer()
        adjusted_plan = optimizer.adjust_schedule(
            current_plan=request.current_plan,
            performance_history=request.performance_history,
            user_preferences=request.user_preferences
        )
        return ScheduleAdjustResponse(adjusted_plan=adjusted_plan)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ml/answer-doubt", response_model=DoubtAnswerResponse)
async def answer_doubt(request: DoubtAnswerRequest):
    """
    Answer student doubt using RAG (Retrieval-Augmented Generation).
    
    Architecture:
    1. Embedding model (sentence-transformers) encodes the question
    2. Vector store (FAISS/pgvector) retrieves relevant educational content
    3. Generator model (fine-tuned T5/Phi-2) generates the answer
    
    All models are self-hosted. NO external AI APIs.
    """
    try:
        tutor = get_rag_tutor()
        result = tutor.answer_doubt(
            user_id=request.user_id,
            subject_id=request.subject_id,
            question_text=request.question_text,
            top_k=request.top_k
        )
        return DoubtAnswerResponse(
            answer=result["answer"],
            confidence=result["confidence"],
            sources=result["sources"],
            subject_id=result["subject_id"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check for ML service."""
    return {
        "status": "ok",
        "service": "vidyajyoti-ml",
        "models_loaded": True  # Would check actual model status in production
    }
