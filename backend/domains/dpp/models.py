import uuid
from datetime import datetime
from typing import Optional, Dict

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, String, Numeric, ForeignKey, DateTime, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.infrastructure.database import Base


class DppRecord(Base):
    """
    최종 발행된 DPP 기록을 관리하는 모델이에요.
    schema.sql 영역 9의 dpp_records 테이블과 1:1로 매핑됩니다.
    """
    __tablename__ = "dpp_records"

    dpp_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"), default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("batches.batch_id"), nullable=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.product_id"), nullable=True)
    
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=True)  # 허용값: 'issued', 'revoked' (schema.sql 기준)
    
    carbon_footprint: Mapped[float] = mapped_column(Numeric(10, 4), nullable=True)
    recycled_content: Mapped[dict] = mapped_column(JSONB, nullable=True)
    qr_code_url: Mapped[str] = mapped_column(String(500), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=True)
    
    approved_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True)


# ==============================================================================
# API 응답 스키마 (DTO)
# ==============================================================================

class DppRecordResponse(BaseModel):
    dpp_id: uuid.UUID
    batch_id: Optional[uuid.UUID] = None
    product_id: Optional[uuid.UUID] = None
    issued_at: Optional[datetime] = None
    status: Optional[str] = None
    carbon_footprint: Optional[float] = None
    recycled_content: Optional[dict] = None
    qr_code_url: Optional[str] = None
    payload: Optional[dict] = None
    approved_by: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)

class ReadinessResponse(BaseModel):
    product_id: uuid.UUID
    readiness_score: float
    breakdown: Dict[str, bool]