import uuid
from datetime import datetime
from typing import Optional, Dict, List

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, String, Numeric, ForeignKey, DateTime, text, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
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


class TransmissionLog(Base):
    """
    대외 전송 로그 + 도달확인(Ack) 모델이에요.
    schema.sql의 transmission_logs 테이블과 1:1로 매핑됩니다.
    (구 DppDeliveryHistory가 가리키던 dpp_delivery_history 테이블은 schema.sql에 없어 즉사하던 버그였어요.
     SSOT 원칙에 따라 동일 역할의 transmission_logs로 재배선했습니다.
     recipient_id는 customer/supplier/authority polymorphic — FK 없음(스키마 의도).
     다른 도메인(공급망/당국 대응 등)도 같은 테이블을 함께 쓸 수 있어 extend_existing 처리해요.)
    """
    __tablename__ = "transmission_logs"
    __table_args__ = {'extend_existing': True}

    transmission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"), default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("batches.batch_id"), nullable=True)
    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    recipient_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'customer' | 'supplier' | 'authority'
    recipient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)  # polymorphic, FK 없음(의도)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    transmission_type: Mapped[str] = mapped_column(String(30), nullable=False)  # 'dpp_report' 등
    status: Mapped[str] = mapped_column(String(20), default="sent", server_default="sent", nullable=True)
    payload_summary: Mapped[str] = mapped_column(Text, nullable=True)
    attachment_urls: Mapped[dict] = mapped_column(JSONB, nullable=True)
    ack_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=True)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


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


# ==============================================================================
# §6 프론트 계약 DTO
# ==============================================================================

class RecycledContentOut(BaseModel):
    co: Optional[float] = None
    ni: Optional[float] = None
    li: Optional[float] = None


class DppRecordBriefOut(BaseModel):
    """6.1a 목록 응답"""
    dpp_id: Optional[uuid.UUID] = None
    product_id: Optional[uuid.UUID] = None
    product_code: Optional[str] = None
    model_name: Optional[str] = None
    manufacturer: Optional[str] = None
    destination: Optional[str] = None
    approved_by: Optional[uuid.UUID] = None
    status: Optional[str] = None
    issued_at: Optional[datetime] = None
    carbon_footprint: Optional[float] = None
    recycled_content: Optional[RecycledContentOut] = None

    model_config = ConfigDict(from_attributes=True)


class DppRecordDetailOut(DppRecordBriefOut):
    """6.1b 단건 상세 응답"""
    serial_number: Optional[str] = None
    produced_at_factory_id: Optional[str] = None
    produced_at: Optional[datetime] = None
    capacity: Optional[float] = None
    supply_chain_version: Optional[str] = None
    dpp_version: Optional[str] = None


class ReadinessCheckOut(BaseModel):
    key: str
    label: str
    passed: bool


class ReadinessBlockerOut(BaseModel):
    name: str
    related_doc: Optional[str] = None
    due_date: Optional[datetime] = None
    severity: Optional[str] = None


class ReadinessBriefOut(BaseModel):
    """6.3a readiness 응답 (프론트 계약)"""
    product_id: uuid.UUID
    product_name: Optional[str] = None
    readiness: float
    checks: List[ReadinessCheckOut] = []
    blockers: List[ReadinessBlockerOut] = []


class HeldProductOut(BaseModel):
    """6.3b / 6.2b 보류 제품 목록"""
    product_id: uuid.UUID
    product_name: Optional[str] = None
    destination: Optional[str] = None
    readiness: Optional[float] = None
    blocker_count: int = 0
    status: Optional[str] = None
    blocker_key: Optional[str] = None
    last_updated_at: Optional[datetime] = None


class DppStatusOut(BaseModel):
    """6.2a 카운트 요약"""
    ready_count: int = 0
    hold_count: int = 0
    hitl_count: int = 0
    blocker_count: int = 0
    issued_count: int = 0


class DppBlockersOut(BaseModel):
    """6.2c 블로커 건수"""
    feoc: int = 0
    origin: int = 0
    hitl: int = 0
    audit: int = 0


class CarbonTrendSeriesOut(BaseModel):
    name: str
    points: List[float] = []


class CarbonTrendOut(BaseModel):
    """6.2d 탄소발자국 트렌드"""
    labels: List[str] = []
    series: List[CarbonTrendSeriesOut] = []


class RecycledContentAvgOut(BaseModel):
    """6.2e 재활용 함량 평균"""
    co_avg: Optional[float] = None
    ni_avg: Optional[float] = None
    li_avg: Optional[float] = None


class IssueRequest(BaseModel):
    """6.3c issue 요청 바디"""
    approver: Optional[str] = None