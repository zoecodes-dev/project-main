"""
events/types.py  ← 이 파일만 팀 전체가 공유 (스펙 7장 계약 + backend_md_additions I·E-1절)

도메인 간 직접 import 금지. 통신은 이 이벤트 타입 + event_bus.publish()로만.
각자 자기 이벤트 타입을 여기에 추가한다. (총 30종)

payload는 JSON 직렬화 가능해야 하며, event_bus.publish(event_name, payload)에
넣을 dict는 dataclasses.asdict()로 변환해 전달한다.

출처:
- spec 7장 본문/표: Product, Supplier, Submission, Validation, Risk,
  GeoRiskDetected, ComplianceCompleted, HITL, DPP
- backend_md_additions I·E-1절: RiskProfileUpdated, FactoryRegulationChanged,
  SubmissionStatusChanged, OriginCertExpiring, TrainingOverdue
- 폴더명은 verification/ 이지만 이벤트는 Validation* 이다 (spec 7장 기준).
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

# UTC 시간 생성 헬퍼 함수
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

# ============================================================
# Product (C)
# ============================================================
@dataclass
class ProductCreatedEvent:
    product_id: Optional[UUID] = None
    event_name: str = "ProductCreated"
    occurred_at: datetime = field(default_factory=_now_utc)

@dataclass
class LotCreatedEvent:
    lot_id: Optional[UUID] = None
    product_id: Optional[UUID] = None
    event_name: str = "LotCreated"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class BOMMappedEvent:
    product_id: Optional[UUID] = None
    bom_version_id: Optional[UUID] = None
    event_name: str = "BOMMapped"
    occurred_at: datetime = field(default_factory=_now_utc)


# ============================================================
# Supplier (B)
# ============================================================
@dataclass
class SupplierInvitedEvent:
    supplier_id: Optional[UUID] = None
    email: Optional[str] = None
    sla_due_date: Optional[datetime] = None
    event_name: str = "SupplierInvited"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class SupplierConnectedEvent:
    supplier_id: Optional[UUID] = None
    tier: Optional[int] = None
    event_name: str = "SupplierConnected"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class SupplierStatusChangedEvent:
    supplier_id: Optional[UUID] = None
    new_status: Optional[str] = None
    event_name: str = "SupplierStatusChanged"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class RiskProfileUpdatedEvent:
    """
    overall_risk_score 변경 시 발행. (backend_md_additions I절)
    발행: B → 수신: A(StateGraph 참조)
    """
    supplier_id: Optional[UUID] = None
    overall_risk_score: Optional[float] = None
    event_name: str = "RiskProfileUpdated"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class FactoryRegulationChangedEvent:
    """
    공장의 applicable_regulations 컬럼 수정 시 발행. (backend_md_additions I절)
    발행: B → 수신: C(Compliance)
    """
    factory_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    applicable_regulations: list = field(default_factory=list)
    event_name: str = "FactoryRegulationChanged"
    occurred_at: datetime = field(default_factory=_now_utc)


# ============================================================
# Submission (E)
# ============================================================
@dataclass
class SubmissionRequestedEvent:
    request_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    due_date: Optional[datetime] = None
    event_name: str = "SubmissionRequested"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class SubmissionStartedEvent:
    request_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    event_name: str = "SubmissionStarted"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class SubmissionCompletedEvent:
    request_id: Optional[UUID] = None
    batch_id: Optional[UUID] = None
    file_urls: list = field(default_factory=list)
    event_name: str = "SubmissionCompleted"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class SubmissionRejectedEvent:
    request_id: Optional[UUID] = None
    reason: Optional[str] = None
    event_name: str = "SubmissionRejected"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class SubmissionApprovedEvent:
    request_id: Optional[UUID] = None
    batch_id: Optional[UUID] = None
    event_name: str = "SubmissionApproved"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class SubmissionStatusChangedEvent:
    """
    submission 상태 전이 시 자동 발행 (audit 자동 기록용).
    (backend_md_additions E-1절 / spec 7장)
    발행: E → 수신: Audit, Notification
    """
    request_id: Optional[UUID] = None
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    event_name: str = "SubmissionStatusChanged"
    occurred_at: datetime = field(default_factory=_now_utc)


# ============================================================
# Validation (E) — 폴더는 verification/, 이벤트는 Validation*
# ============================================================
@dataclass
class ValidationStartedEvent:
    batch_id: Optional[UUID] = None
    rules_applied: list = field(default_factory=list)
    event_name: str = "ValidationStarted"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class ValidationFailedEvent:
    batch_id: Optional[UUID] = None
    violated_rules: list = field(default_factory=list)
    event_name: str = "ValidationFailed"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class ValidationCompletedEvent:
    batch_id: Optional[UUID] = None
    results: list = field(default_factory=list)
    event_name: str = "ValidationCompleted"
    occurred_at: datetime = field(default_factory=_now_utc)


# ============================================================
# SupplyChain / Geo (D · 영수) — 본 도메인에서 실제 발행
# ============================================================
@dataclass
class GeoRiskDetectedEvent:
    """
    고위험 지역 판정 또는 좌표 불일치 발견 시 발행.
    발행: D(SupplyChain/Geo Audit) → 수신: A(Supervisor 라우팅), Risk
    spec 7장 payload 핵심 필드: batch_id, factory_id, risk_type
    """
    batch_id: Optional[UUID] = None
    factory_id: Optional[UUID] = None
    risk_type: Optional[str] = None        # "xinjiang" | "eudr" | "country_mismatch"
    supplier_id: Optional[UUID] = None
    company_name: Optional[str] = None
    coordinates: Optional[str] = None
    event_name: str = "GeoRiskDetected"
    occurred_at: datetime = field(default_factory=_now_utc)


# ============================================================
# Risk (E)
# ============================================================
@dataclass
class RiskDetectedEvent:
    batch_id: Optional[UUID] = None
    risk_score: Optional[float] = None
    event_name: str = "RiskDetected"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class RiskEscalatedEvent:
    batch_id: Optional[UUID] = None
    reason: Optional[str] = None
    event_name: str = "RiskEscalated"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class RiskResolvedEvent:
    batch_id: Optional[UUID] = None
    resolved_by: Optional[UUID] = None
    event_name: str = "RiskResolved"
    occurred_at: datetime = field(default_factory=_now_utc)


# ============================================================
# Compliance (C)
# ============================================================
@dataclass
class ComplianceCompletedEvent:
    batch_id: Optional[UUID] = None
    verdicts: dict = field(default_factory=dict)
    event_name: str = "ComplianceCompleted"
    occurred_at: datetime = field(default_factory=_now_utc)


# ============================================================
# HITL (A)
# ============================================================
@dataclass
class HITLRequestedEvent:
    batch_id: Optional[UUID] = None
    reason: Optional[str] = None
    reviewer_id: Optional[UUID] = None
    event_name: str = "HITLRequested"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class HITLAssignedEvent:
    batch_id: Optional[UUID] = None
    reviewer_id: Optional[UUID] = None
    event_name: str = "HITLAssigned"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class HITLApprovedEvent:
    batch_id: Optional[UUID] = None
    reviewer_id: Optional[UUID] = None
    note: Optional[str] = None
    event_name: str = "HITLApproved"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class HITLRejectedEvent:
    batch_id: Optional[UUID] = None
    reviewer_id: Optional[UUID] = None
    reason: Optional[str] = None
    event_name: str = "HITLRejected"
    occurred_at: datetime = field(default_factory=_now_utc)


# ============================================================
# DPP (E)
# ============================================================
@dataclass
class DPPReadinessUpdatedEvent:
    product_id: Optional[UUID] = None
    readiness_score: Optional[float] = None
    event_name: str = "DPPReadinessUpdated"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class DPPIssuedEvent:
    dpp_id: Optional[UUID] = None
    product_id: Optional[UUID] = None
    qr_code_url: Optional[str] = None
    event_name: str = "DPPIssued"
    occurred_at: datetime = field(default_factory=_now_utc)


# ============================================================
# Origin (B · 스케줄러)
# ============================================================
@dataclass
class OriginCertExpiringEvent:
    """
    원산지(포괄)확인서 만료 임박 감지. (backend_md_additions I절)
    조건: expires_at < now() + 30일
    발행: B(스케줄러) → 수신: E(Readiness 재계산), Notification
    """
    certificate_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    expires_at: Optional[datetime] = None
    event_name: str = "OriginCertExpiring"
    occurred_at: datetime = field(default_factory=_now_utc)


# ============================================================
# Training (B · 스케줄러)
# ============================================================
@dataclass
class TrainingOverdueEvent:
    """
    협력사 교육 이수 지연 감지. (backend_md_additions I절)
    조건: due_date < now() AND status != completed
    발행: B(스케줄러) → 수신: Notification
    """
    training_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    due_date: Optional[datetime] = None
    event_name: str = "TrainingOverdue"
    occurred_at: datetime = field(default_factory=_now_utc)