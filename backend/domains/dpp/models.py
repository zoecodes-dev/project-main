import uuid
from datetime import datetime
from typing import Optional, Dict

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, String, Numeric, ForeignKey, DateTime, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.infrastructure.database import Base
from backend.domains.audit import models as _audit_models  # registers tenants/users tables
from backend.domains.product import models as _product_models  # registers products/bom tables


class Batch(Base):
    """
    AI 자동화 파이프라인(LangGraph)이 실행되는 단위(배치) 모델이에요.
    schema.sql 영역 9의 batches 테이블과 1:1로 매핑됩니다.
    """
    __tablename__ = "batches"
    __table_args__ = {'extend_existing': True}

    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"), default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.product_id"), nullable=True)
    bom_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bom_versions.bom_version_id"), nullable=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=True)
    
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=True)
    destination: Mapped[str] = mapped_column(String(2), nullable=True)
    current_stage: Mapped[str] = mapped_column(String(50), default="stage_queued", server_default="stage_queued", nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="batch_processing", server_default="batch_processing", nullable=True)
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=True)
    
    source_system: Mapped[str] = mapped_column(String(100), default="MES", server_default="MES", nullable=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=True)


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
    status: Mapped[str] = mapped_column(String(20), nullable=True)  # 허용값: 'dpp_issued', 'dpp_revoked' (schema.sql 기준)
    
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
