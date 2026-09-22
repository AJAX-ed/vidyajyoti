from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from ..database import get_db
from ..models import Exam, Subject
from ..schemas import ExamResponse

router = APIRouter(prefix="/api/exams", tags=["exams"])


@router.get("", response_model=List[ExamResponse])
async def get_exams(db: AsyncSession = Depends(get_db)):
    """
    Get all available exams with their subjects.
    Returns a list of exams (JEE Main, NEET, CBSE, etc.) with associated subjects.
    """
    # For now, return mock data. In production, this would query the database.
    # Seed data would be inserted into the exams and subjects tables.
    
    mock_exams = [
        {
            "id": 1,
            "code": "JEE_MAIN",
            "name": "JEE Main",
            "description": "Joint Entrance Examination - Main",
            "subjects": [
                {"id": 1, "exam_id": 1, "name": "Physics", "code": "PHY"},
                {"id": 2, "exam_id": 1, "name": "Chemistry", "code": "CHEM"},
                {"id": 3, "exam_id": 1, "name": "Mathematics", "code": "MATH"},
            ]
        },
        {
            "id": 2,
            "code": "JEE_ADVANCED",
            "name": "JEE Advanced",
            "description": "Joint Entrance Examination - Advanced",
            "subjects": [
                {"id": 4, "exam_id": 2, "name": "Physics", "code": "PHY"},
                {"id": 5, "exam_id": 2, "name": "Chemistry", "code": "CHEM"},
                {"id": 6, "exam_id": 2, "name": "Mathematics", "code": "MATH"},
            ]
        },
        {
            "id": 3,
            "code": "NEET",
            "name": "NEET",
            "description": "National Eligibility cum Entrance Test",
            "subjects": [
                {"id": 7, "exam_id": 3, "name": "Physics", "code": "PHY"},
                {"id": 8, "exam_id": 3, "name": "Chemistry", "code": "CHEM"},
                {"id": 9, "exam_id": 3, "name": "Biology", "code": "BIO"},
            ]
        },
        {
            "id": 4,
            "code": "CBSE_11",
            "name": "CBSE Class 11",
            "description": "CBSE Board Class 11",
            "subjects": [
                {"id": 10, "exam_id": 4, "name": "Physics", "code": "PHY"},
                {"id": 11, "exam_id": 4, "name": "Chemistry", "code": "CHEM"},
                {"id": 12, "exam_id": 4, "name": "Mathematics", "code": "MATH"},
                {"id": 13, "exam_id": 4, "name": "Biology", "code": "BIO"},
                {"id": 14, "exam_id": 4, "name": "English", "code": "ENG"},
            ]
        },
        {
            "id": 5,
            "code": "CBSE_12",
            "name": "CBSE Class 12",
            "description": "CBSE Board Class 12",
            "subjects": [
                {"id": 15, "exam_id": 5, "name": "Physics", "code": "PHY"},
                {"id": 16, "exam_id": 5, "name": "Chemistry", "code": "CHEM"},
                {"id": 17, "exam_id": 5, "name": "Mathematics", "code": "MATH"},
                {"id": 18, "exam_id": 5, "name": "Biology", "code": "BIO"},
                {"id": 19, "exam_id": 5, "name": "English", "code": "ENG"},
            ]
        },
        {
            "id": 6,
            "code": "SSC",
            "name": "SSC",
            "description": "Staff Selection Commission",
            "subjects": [
                {"id": 20, "exam_id": 6, "name": "General Awareness", "code": "GA"},
                {"id": 21, "exam_id": 6, "name": "Quantitative Aptitude", "code": "QA"},
                {"id": 22, "exam_id": 6, "name": "Reasoning", "code": "REASON"},
                {"id": 23, "exam_id": 6, "name": "English", "code": "ENG"},
            ]
        },
    ]
    
    return mock_exams
