import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID

from backend.infrastructure.database import Base

class HitlReview(Base):
    __tablename__ = 'hitl_reviews'

    review_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id = Column(UUID(as_uuid=True), nullable=False)
    reason = Column(String(100), nullable=False)
    trigger_stage = Column(String(50), nullable=False)
    assigned_to = Column(UUID(as_uuid=True), nullable=True)
    
    # 상태와 결정 결과 분리
    status = Column(String(30), default='hitl_pending')
    resolution = Column(String(20), nullable=True)
    
    decision_text = Column(Text, nullable=True)
    decided_by = Column(UUID(as_uuid=True), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    
    # utcnow() 대신 timezone.utc 사용
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
