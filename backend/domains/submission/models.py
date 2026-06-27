from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional, List
from decimal import Decimal
import uuid
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Integer, String, DateTime, Text, ForeignKey, Enum, func, Numeric, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

# 인프라 레이어 의존성 주입 (Modular Monolith 구조 준수)
from backend.infrastructure.database import Base


# ==============================================================================
# 1. 비즈니스 안정성을 위한 Enum 타입 정의 (인계 문서 불변 명세 기준)
# ==============================================================================

class ResponseStatus(str, PyEnum):
    PENDING = "response_pending"
    RESPONDED = "response_responded"
    OVERDUE = "response_overdue"
    ESCALATED = "response_escalated"


class SubmissionStatus(str, PyEnum):
    REQUESTED = "submission_requested"
    IN_PROGRESS = "submission_in_progress"
    SUBMITTED = "submission_submitted"
    REVIEW = "submission_review"
    APPROVED = "submission_approved"
    REWORK = "submission_rework"
    REJECTED = "submission_rejected"


# ==============================================================================
# 2. DataRequestLog 테이블 매핑 ORM 모델 (schema.sql 철자 기준)
# ==============================================================================

class DataRequestLog(Base):
    __tablename__ = "data_request_log"

    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4(), default=uuid.uuid4) # 요청 고유 ID (UUID)
    requester_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True) # 요청을 발송한 원청사 담당자 ID
    target_supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"), nullable=True) # 데이터를 제출해야 하는 협력사 ID
    requested_data_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # 요청하는 ESG 데이터 종류 (schema.sql VARCHAR(100) 일치)
    requested_at: Mapped[Optional[datetime]] = mapped_column(                               # 요청서 발송 시각 (자동 입력되는 표준 UTC 시간)
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True) # 데이터 제출 마감일 (SLA 관리 기준점)
    response_status: Mapped[Optional[ResponseStatus]] = mapped_column(                      # 단순 제출 여부 상태 (PENDING / RESPONDED / OVERDUE / ESCALATED)
        Enum(ResponseStatus, native_enum=False, length=30, values_callable=lambda e: [m.value for m in e]), default=ResponseStatus.PENDING, server_default="response_pending", nullable=True
    )
    reminder_count: Mapped[Optional[int]] = mapped_column(Integer, default=0, server_default="0", nullable=True)         # 마감 미준수 협력사 독촉장(리마인드) 발송 횟수
    last_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True) # 가장 최근에 독촉장을 보낸 시각 (미발송 시 Null)
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)     # 협력사가 최종 제출 버튼을 누른 시각 (미제출 시 Null)
    submission_status: Mapped[Optional[SubmissionStatus]] = mapped_column(                  # 플랫폼 핵심 9개 상태 머신 프로세스 단계 관리
        Enum(SubmissionStatus, native_enum=False, length=30, values_callable=lambda e: [m.value for m in e]), default=SubmissionStatus.REQUESTED, server_default="submission_requested", nullable=True
    )
    # submit 시 생성된 batch_id 보관 — approve 시 파이프라인 enqueue에 사용.
    # DDL: ALTER TABLE data_request_log ADD COLUMN batch_id UUID; (D 영수 담당)
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_archived: Mapped[Optional[bool]] = mapped_column(Boolean, default=False, server_default="false", nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=True)

    # SubmissionStatusHistory 모델과의 1:N 관계 설정 (상태 변경 추적용)
    histories: Mapped[list["SubmissionStatusHistory"]] = relationship(
        "SubmissionStatusHistory", back_populates="request", cascade="all, delete-orphan"
    )


# ==============================================================================
# 3. SubmissionStatusHistory 테이블 매핑 ORM 모델 (schema.sql 철자 기준)
# ==============================================================================

class SubmissionStatusHistory(Base):
    __tablename__ = "submission_status_history"

    history_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4(), default=uuid.uuid4)     # 감사 기록 고유 ID (UUID)
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("data_request_log.request_id", ondelete="CASCADE"), nullable=True) # 연결된 데이터 요청서 번호 (외래키)
    from_status: Mapped[Optional[SubmissionStatus]] = mapped_column(Enum(SubmissionStatus, native_enum=False, length=30, values_callable=lambda e: [m.value for m in e]), nullable=True) # 변경되기 전의 직전 프로세스 상태
    to_status: Mapped[SubmissionStatus] = mapped_column(Enum(SubmissionStatus, native_enum=False, length=30, values_callable=lambda e: [m.value for m in e]), nullable=False)   # 변경된 후의 새로운 프로세스 상태
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True) # 상태 전이를 발생시킨 주체 ID (유저 혹은 시스템 ID)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)                      # 상태 변경 및 반려 사유 설명 (텍스트, 공란 허용)
    changed_at: Mapped[Optional[datetime]] = mapped_column(                                 # 상태 전이가 일어난 정확한 시각 (자동 입력 표준 UTC)
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )

    # DataRequestLog 모델과의 N:1 관계 설정
    request: Mapped["DataRequestLog"] = relationship("DataRequestLog", back_populates="histories")


# ==============================================================================
# 4. DataCompletenessStatus & Notification 테이블 매핑 ORM 모델 (영역 11)
# ==============================================================================

class DataCompletenessStatus(Base):
    __tablename__ = "data_completeness_status"

    status_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4(), default=uuid.uuid4)
    entity_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    required_field_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    filled_field_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    missing_fields: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    last_updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    last_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=True)


class Notification(Base):
    __tablename__ = "notifications"

    notification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4(), default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    notification_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    dedup_key: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=True)


class SubmissionDocument(Base):
    __tablename__ = "submission_documents"

    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4(), default=uuid.uuid4)
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("data_request_log.request_id", ondelete="CASCADE"), nullable=True)
    supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.supplier_id", ondelete="CASCADE"), nullable=True)
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    doc_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    file_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=True)


class DocumentExtractionResult(Base):
    __tablename__ = "document_extraction_results"

    extraction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4(), default=uuid.uuid4)
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("data_request_log.request_id", ondelete="CASCADE"), nullable=True)
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("submission_documents.document_id", ondelete="CASCADE"), nullable=True)
    parsed_fields: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    confidence_map: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    unparsed_fields: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    supplier_confirmed: Mapped[Optional[bool]] = mapped_column(Boolean, default=False, server_default="false", nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=True)


class ProcessedJob(Base):
    __tablename__ = "processed_jobs"

    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    queue_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(20), default="processing", server_default="processing", nullable=True)
    retry_count: Mapped[Optional[int]] = mapped_column(Integer, default=0, server_default="0", nullable=True)
    result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True)


# ==============================================================================
# 5. 백엔드 방어용 상태 전이 매트릭스 딕셔너리 정의 (9개 상태 모두 정의)
# ==============================================================================

SUBMISSION_TRANSITIONS = {
    SubmissionStatus.REQUESTED:   [SubmissionStatus.IN_PROGRESS],
    SubmissionStatus.IN_PROGRESS: [SubmissionStatus.SUBMITTED],
    SubmissionStatus.SUBMITTED:   [SubmissionStatus.REVIEW],
    SubmissionStatus.REVIEW:      [SubmissionStatus.APPROVED, SubmissionStatus.REWORK, SubmissionStatus.REJECTED],
    SubmissionStatus.REWORK:      [SubmissionStatus.IN_PROGRESS],
    SubmissionStatus.APPROVED:    [],
    SubmissionStatus.REJECTED:    [],
}

# ==============================================================================
# 6. Pydantic DTO (Data Transfer Object) 모델 정의
# ==============================================================================

class DataRequestCreateRequest(BaseModel):
    """
    [DTO] POST /data-requests 요청 Payload 스키마.
    클라이언트(또는 다른 서비스)가 새로운 데이터 제출을 요청할 때 전달해야 하는 최소 필수 데이터입니다.
    """
    # requester_user_id/actor_id 미제공 시 라우터가 토큰의 현재 사용자로 채운다.
    requester_user_id: Optional[uuid.UUID] = None
    target_supplier_id: uuid.UUID
    requested_data_type: str
    due_date: Optional[datetime] = None
    actor_id: Optional[uuid.UUID] = None

class DataRequestResponse(BaseModel):
    """
    [DTO] API 응답용 스키마.
    ORM 모델인 DataRequestLog를 직렬화하여 클라이언트에게 안전하게 반환합니다.
    from_attributes = True 설정을 통해 SQLAlchemy 객체에서 바로 값을 읽어올 수 있습니다.
    """
    request_id: uuid.UUID
    requester_user_id: Optional[uuid.UUID] = None
    target_supplier_id: Optional[uuid.UUID] = None
    requested_data_type: Optional[str] = None
    requested_at: Optional[datetime] = None
    due_date: Optional[datetime] = None
    response_status: Optional[ResponseStatus] = None
    submission_status: Optional[SubmissionStatus] = None

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

class SubmitDataRequest(BaseModel):
    actor_id: uuid.UUID
    product_id: uuid.UUID
    destination: str
    file_urls: Optional[List[str]] = None
    confirmed_fields: Optional[dict] = None

class ActionDataRequest(BaseModel):
    actor_id: uuid.UUID
    reason: Optional[str] = None

class CompletenessResponse(BaseModel):
    completion_rate: Optional[Decimal] = None
    missing_fields: Optional[dict] = None
    
    model_config = ConfigDict(from_attributes=True)

class TimelineHistoryResponse(BaseModel):
    history_id: uuid.UUID
    request_id: Optional[uuid.UUID] = None
    from_status: Optional[SubmissionStatus] = None
    to_status: SubmissionStatus
    actor_id: Optional[uuid.UUID] = None
    reason: Optional[str] = None
    changed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


# ==============================================================================
# 7. /submissions 전용 DTO (§4.1 프론트 계약)
# ==============================================================================

class SubmissionFileOut(BaseModel):
    file_id: uuid.UUID
    file_name: Optional[str] = None
    size_bytes: Optional[int] = None


class SubmissionCheckOut(BaseModel):
    label: str
    result: str  # pass | review | fail
    reason: Optional[str] = None


class SubmissionBriefOut(BaseModel):
    """4.1a 목록 응답"""
    submission_id: uuid.UUID
    supplier_id: Optional[uuid.UUID] = None
    supplier_name: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None
    due_date: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    file_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class SubmissionDetailOut(SubmissionBriefOut):
    """4.1b 상세 응답"""
    data_source: Optional[str] = None
    supplier_contact: Optional[str] = None
    reviewer_name: Optional[str] = None
    files: List[SubmissionFileOut] = []
    checks: List[SubmissionCheckOut] = []
    related_pos: List = []


class SubmissionActionIn(BaseModel):
    """4.1c/d/e 요청 바디"""
    reason: Optional[str] = None