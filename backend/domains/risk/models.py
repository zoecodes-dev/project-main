from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, Numeric, Date, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.types import TIMESTAMP

from backend.infrastructure.database import Base


class RiskProfile(Base):
    """
    협력사별 종합 리스크 평가 결과 프로필 (schema.sql의 supplier_risk_profiles 1:1 매핑)
    - 가점식 모델(+50, +30, +15)에 따라 overall_risk_score가 누적됩니다.
    - 70점 이상 시 risk_level이 'critical'로 전이되며 HITL 회부의 기준이 됩니다.
    """
    __tablename__ = "supplier_risk_profiles"
    __table_args__ = {'extend_existing': True}

    profile_id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4())
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.supplier_id", ondelete="CASCADE"), unique=True)
    
    overall_risk_score = Column(Integer, default=0)
    risk_level = Column(String(20), default="low")
    
    feoc_status = Column(String(20), default="unknown")
    feoc_direct_ownership = Column(Numeric(5, 2))
    feoc_indirect_ownership = Column(Numeric(5, 2))
    feoc_last_assessed_at = Column(TIMESTAMP(timezone=True))
    feoc_cert_expiry = Column(Date)
    
    is_high_risk_flag = Column(Boolean, default=False)
    high_risk_reasons = Column(JSONB)
    last_risk_review_at = Column(TIMESTAMP(timezone=True))
    
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))