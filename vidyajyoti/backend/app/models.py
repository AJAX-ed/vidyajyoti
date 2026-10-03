from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Float, Text, Boolean
from sqlalchemy.orm import relationship
from .database import Base


class User(Base):
    """User model for storing student/admin accounts."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=True)
    role = Column(String(50), default="student")  # student, admin
    created_at = Column(DateTime, default=datetime.utcnow)
    onboarding_data = Column(JSON, nullable=True)  # Stores quiz answers
    day_plan = Column(JSON, nullable=True)  # Stores generated day plan
    
    # Relationships
    doubts = relationship("Doubt", back_populates="user")
    points_transactions = relationship("PointsTransaction", back_populates="user")
    battleground_participants = relationship("BattlegroundParticipant", back_populates="user")


class Exam(Base):
    """Exam model for storing exam types (JEE, NEET, CBSE, etc.)."""
    __tablename__ = "exams"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False)  # e.g., "JEE_MAIN"
    name = Column(String(255), nullable=False)  # e.g., "JEE Main"
    description = Column(Text, nullable=True)
    
    # Relationships
    subjects = relationship("Subject", back_populates="exam")
    battleground_sessions = relationship("BattlegroundSession", back_populates="exam")


class Subject(Base):
    """Subject model for storing subjects within exams."""
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True, index=True)
    exam_id = Column(Integer, ForeignKey("exams.id"), nullable=False)
    name = Column(String(255), nullable=False)  # e.g., "Physics"
    code = Column(String(50), nullable=False)  # e.g., "PHY"
    
    # Relationships
    exam = relationship("Exam", back_populates="subjects")
    doubts = relationship("Doubt", back_populates="subject")
    battleground_sessions = relationship("BattlegroundSession", back_populates="subject")


class BattlegroundSession(Base):
    """Model for gamified battleground quiz sessions."""
    __tablename__ = "battleground_sessions"

    id = Column(Integer, primary_key=True, index=True)
    exam_id = Column(Integer, ForeignKey("exams.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    total_questions = Column(Integer, default=10)
    duration_minutes = Column(Integer, default=30)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    exam = relationship("Exam", back_populates="battleground_sessions")
    subject = relationship("Subject", back_populates="battleground_sessions")
    participants = relationship("BattlegroundParticipant", back_populates="session")


class BattlegroundParticipant(Base):
    """Model for tracking user participation in battleground sessions."""
    __tablename__ = "battleground_participants"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("battleground_sessions.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    score = Column(Integer, default=0)
    coins_earned = Column(Integer, default=0)
    completed_at = Column(DateTime, nullable=True)
    
    # Relationships
    session = relationship("BattlegroundSession", back_populates="participants")
    user = relationship("User", back_populates="battleground_participants")


class Doubt(Base):
    """Model for storing student doubts/questions."""
    __tablename__ = "doubts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    title = Column(String(255), nullable=False)
    question_text = Column(Text, nullable=False)
    answer_text = Column(Text, nullable=True)  # Filled by AI tutor or manual response
    status = Column(String(50), default="pending")  # pending, answered, resolved
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="doubts")
    subject = relationship("Subject", back_populates="doubts")


class PointsTransaction(Base):
    """Model for tracking points and coins transactions."""
    __tablename__ = "points_transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    type = Column(String(50), nullable=False)  # quiz_complete, streak_bonus, doubt_asked, etc.
    delta_points = Column(Integer, default=0)
    delta_coins = Column(Integer, default=0)
    metadata = Column(JSON, nullable=True)  # Additional context about the transaction
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="points_transactions")
