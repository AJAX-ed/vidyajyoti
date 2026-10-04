from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["exams"])

# Static seed list (DB-backed version reads from exams/subjects tables).
EXAMS = [
    {
        "id": 1, "code": "JEE_MAIN", "name": "JEE Main",
        "description": "Engineering entrance — Physics, Chemistry, Maths.",
        "subjects": [
            {"id": 1, "name": "Physics", "code": "PHY"},
            {"id": 2, "name": "Chemistry", "code": "CHE"},
            {"id": 3, "name": "Mathematics", "code": "MTH"},
        ],
    },
    {
        "id": 2, "code": "NEET", "name": "NEET",
        "description": "Medical entrance — Physics, Chemistry, Biology.",
        "subjects": [
            {"id": 4, "name": "Physics", "code": "PHY"},
            {"id": 5, "name": "Chemistry", "code": "CHE"},
            {"id": 6, "name": "Biology", "code": "BIO"},
        ],
    },
    {
        "id": 3, "code": "CBSE_11", "name": "CBSE Class 11",
        "description": "CBSE board curriculum for grade 11.",
        "subjects": [
            {"id": 7, "name": "Physics", "code": "PHY"},
            {"id": 8, "name": "Chemistry", "code": "CHE"},
            {"id": 9, "name": "Mathematics", "code": "MTH"},
        ],
    },
]


@router.get("/exams")
async def list_exams():
    return EXAMS
