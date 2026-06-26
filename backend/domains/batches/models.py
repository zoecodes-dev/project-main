import uuid
from datetime import datetime

from sqlalchemy import Column, String, Numeric, ForeignKey, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.infrastructure.database import Base


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
