from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional
import uuid
from sqlalchemy import Integer, String, DateTime, Text, ForeignKey, Enum, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# 최상위 루트에 위치한 공통 Base 클래스 연동 (인프라 통합 규칙 준수)
from database import Base 


# ==============================================================================
# 1. 비즈니스 안정성을 위한 Enum 타입 정의 (인계 문서 불변 명세 기준)
# ==============================================================================

class ResponseStatus(str, PyEnum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    DELAYED = "DELAYED"


class SubmissionStatus(str, PyEnum):
    PENDING = "pending"
    REQUESTED = "requested"
    IN_PROGRESS = "in-progress"  # 하이픈(-) 구조 불변 명세 반영
    SUBMITTED = "submitted"
    REVIEW = "review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"
    VIOLATION = "violation"


# ==============================================================================
# 2. DataRequestLog 테이블 매핑 ORM 모델 (schema.sql 철자 기준)
# ==============================================================================

class DataRequestLog(Base):
    __tablename__ = "data_request_log"

    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4) # 요청 고유 ID (UUID)
    requester_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)                # 요청을 발송한 원청사 담당자 ID
    target_supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)               # 데이터를 제출해야 하는 협력사 ID
    requested_data_type: Mapped[str] = mapped_column(String(50), nullable=False)            # 요청하는 ESG 데이터 종류 (예: 탄소배출량, 원산지서류)
    requested_at: Mapped[datetime] = mapped_column(                                         # 요청서 발송 시각 (자동 입력되는 표준 UTC 시간)
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)     # 데이터 제출 마감일 (SLA 관리 기준점)
    response_status: Mapped[ResponseStatus] = mapped_column(                                # 단순 제출 여부 상태 (PENDING / SUBMITTED / DELAYED)
        Enum(ResponseStatus), default=ResponseStatus.PENDING, nullable=False
    )
    reminder_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)         # 마감 미준수 협력사 독촉장(리마인드) 발송 횟수
    last_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True) # 가장 최근에 독촉장을 보낸 시각 (미발송 시 Null)
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)     # 협력사가 최종 제출 버튼을 누른 시각 (미제출 시 Null)
    submission_status: Mapped[SubmissionStatus] = mapped_column(                            # 플랫폼 핵심 9개 상태 머신 프로세스 단계 관리
        Enum(SubmissionStatus), default=SubmissionStatus.PENDING, nullable=False
    )

    # SubmissionStatusHistory 모델과의 1:N 관계 설정 (상태 변경 추적용)
    histories: Mapped[list["SubmissionStatusHistory"]] = relationship(
        "SubmissionStatusHistory", back_populates="request", cascade="all, delete-orphan"
    )


# ==============================================================================
# 3. SubmissionStatusHistory 테이블 매핑 ORM 모델 (schema.sql 철자 기준)
# ==============================================================================

class SubmissionStatusHistory(Base):
    __tablename__ = "submission_status_history"

    history_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)     # 감사 기록 고유 ID (UUID)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_request_log.request_id"), nullable=False) # 연결된 데이터 요청서 번호 (외래키)
    from_status: Mapped[SubmissionStatus] = mapped_column(Enum(SubmissionStatus), nullable=False) # 변경되기 전의 직전 프로세스 상태
    to_status: Mapped[SubmissionStatus] = mapped_column(Enum(SubmissionStatus), nullable=False)   # 변경된 후의 새로운 프로세스 상태
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)               # 상태 전이를 발생시킨 주체 ID (유저 혹은 시스템 ID)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)                      # 상태 변경 및 반려 사유 설명 (텍스트, 공란 허용)
    changed_at: Mapped[datetime] = mapped_column(                                           # 상태 전이가 일어난 정확한 시각 (자동 입력 표준 UTC)
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # DataRequestLog 모델과의 N:1 관계 설정
    request: Mapped["DataRequestLog"] = relationship("DataRequestLog", back_populates="histories")


# ==============================================================================
# 4. 백엔드 방어용 상태 전이 매트릭스 딕셔너리 정의 (9개 상태 모두 정의)
# ==============================================================================

SUBMISSION_TRANSITIONS = {
    SubmissionStatus.PENDING:     [SubmissionStatus.REQUESTED],
    SubmissionStatus.REQUESTED:   [SubmissionStatus.IN_PROGRESS],
    SubmissionStatus.IN_PROGRESS: [SubmissionStatus.SUBMITTED],
    SubmissionStatus.SUBMITTED:   [SubmissionStatus.REVIEW],
    SubmissionStatus.REVIEW:      [SubmissionStatus.APPROVED, SubmissionStatus.REJECTED],
    SubmissionStatus.REJECTED:    [SubmissionStatus.IN_PROGRESS],
    SubmissionStatus.APPROVED:    [SubmissionStatus.ARCHIVED, SubmissionStatus.VIOLATION],
    SubmissionStatus.ARCHIVED:    [],
    SubmissionStatus.VIOLATION:   [],
}
