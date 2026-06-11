"""
events/types.py  ← 이 파일만 팀 전체가 공유 (스펙 7장 계약 + backend_md_additions I·E-1절)

도메인 간 직접 import 금지. 통신은 이 이벤트 타입 + event_bus.publish()로만.
각자 자기 이벤트 타입을 여기에 추가한다. (총 30종)

payload는 JSON 직렬화 가능해야 하며, event_bus.publish(event_name, payload)에
넣을 dict는 dataclasses.asdict()로 변환해 전달한다.

출처:
- spec 7장 본문/표: Product, Supplier, Submission, Verification, Risk,
  GeoRiskDetected, ComplianceCompleted, HITL, DPP
- backend_md_additions I·E-1절: RiskProfileUpdated, FactoryRegulationChanged,
  SubmissionStatusChanged, OriginCertExpiring, TrainingOverdue
- 폴더·큐·도메인·state·이벤트 전부 verification으로 통일 (events/types.py 기준).
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, field_validator

# UTC 시간 생성 헬퍼 함수
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

# ============================================================
# Product (C)
# ============================================================
@dataclass
class CustomerImportedEvent:
    """
    고객사(완성차 OEM) UPSERT 완료 시 발행.
    발행: C(Product Ingest) → 수신: 공급망·컴플라이언스 등 downstream.

    발행 순서 규칙:
        CustomerImported → (product UPSERT) → BOMImported → LotImported → ProductImported
        products.customer_id FK 의존 때문에 customer가 먼저 확정되어야 한다.

    is_new: True면 신규 INSERT, False면 기존 row 업데이트(UPSERT 충돌).
        downstream이 "새 고객사 등장" vs "기존 갱신"을 구분할 수 있게 담아요.
    """
    customer_id: Optional[UUID] = None
    customer_code: Optional[str] = None   # 예: 'BMW', 'MERCEDES'
    external_id: Optional[str] = None     # 원천 ERP 식별자
    is_new: bool = True                   # 신규 INSERT=True / UPSERT 갱신=False
    event_name: str = "CustomerImported"
    occurred_at: datetime = field(default_factory=_now_utc)

@dataclass
class ProductImportedEvent:
    product_id: Optional[UUID] = None
    external_id: Optional[str] = None   # 원천 ERP/PLM 식별자
    event_name: str = "ProductImported"
    occurred_at: datetime = field(default_factory=_now_utc)

@dataclass
class LotImportedEvent:
    batch_id: Optional[UUID] = None
    product_id: Optional[UUID] = None
    external_id: Optional[str] = None   # 원천 MES 식별자
    event_name: str = "LotImported"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class BOMImportedEvent:
    product_id: Optional[UUID] = None
    bom_version_id: Optional[UUID] = None
    external_id: Optional[str] = None   # 원천 PLM 식별자
    event_name: str = "BOMImported"
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
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    event_name: str = "SupplierStatusChanged"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class RiskProfileUpdatedEvent:
    """
    overall_risk_score 변경 시 발행. 발행: B / 수신: A(StateGraph 참조).
    payload 핵심 필드: supplier_id, overall_risk_score (+ 수신측 편의로 risk_level 동봉).
    """
    supplier_id: Optional[UUID] = None
    overall_risk_score: Optional[int] = None
    risk_level: Optional[str] = None
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
    submission_mode: str = "file"       # 'file' | 'form' — 폼 직접입력(파일 없음) 케이스 수용 (#9-B/#3)
    file_urls: list = field(default_factory=list)        # submission_mode='file'일 때
    confirmed_fields: dict = field(default_factory=dict) # 협력사가 AI 파싱결과를 확정한 필드 (#3 확인 루프)
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
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    event_name: str = "SubmissionStatusChanged"
    occurred_at: datetime = field(default_factory=_now_utc)


# ============================================================
# Verification (E) — 폴더·큐·도메인·state·이벤트 모두 verification
# ============================================================
@dataclass
class VerificationStartedEvent:
    batch_id: Optional[UUID] = None
    rules_applied: list = field(default_factory=list)
    event_name: str = "VerificationStarted"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class VerificationFailedEvent:
    batch_id: Optional[UUID] = None
    violated_rules: list = field(default_factory=list)
    event_name: str = "VerificationFailed"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class VerificationCompletedEvent:
    batch_id: Optional[UUID] = None
    results: list = field(default_factory=list)
    event_name: str = "VerificationCompleted"
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
    review_id: Optional[UUID] = None
    batch_id: Optional[UUID] = None
    reviewer_id: Optional[UUID] = None
    event_name: str = "HITLAssigned"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class HITLApprovedEvent:
    review_id: Optional[UUID] = None
    batch_id: Optional[UUID] = None
    reviewer_id: Optional[UUID] = None
    note: Optional[str] = None
    event_name: str = "HITLApproved"
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass
class HITLRejectedEvent:
    review_id: Optional[UUID] = None
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
    readiness_breakdown: dict = field(default_factory=dict)
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
    cert_id: Optional[UUID] = None
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
    record_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    due_date: Optional[datetime] = None
    event_name: str = "TrainingOverdue"
    occurred_at: datetime = field(default_factory=_now_utc)

# ============================================================
# ValidationResult + validate_schema (B)
# ============================================================
@dataclass
class ValidationResult:
    """스키마 검증 결과. 누락 필드와 정규화된 값을 담는다."""
    ok: bool
    missing_fields: list[str] = field(default_factory=list)
    normalized: dict = field(default_factory=dict)


# ============================================================
# RecycledMaterialsSchema (B·C 공유 — 도메인 간 계약)
#
# supplier_recycler_details.recycled_materials JSONB 의 공식 구조.
# B(저장)·C(검증)가 동일 클래스를 공유해 key 불일치를 구조적으로 차단한다.
#
# 사용법:
#   저장(B): RecycledMaterialsSchema(co=18.0, ni=7.0).model_dump(exclude_none=True)
#   검증(C): RecycledMaterialsSchema(**row.recycled_materials)
# ============================================================
class RecycledMaterialsSchema(BaseModel):
    """
    supplier_recycler_details.recycled_materials JSONB 의 공식 구조.
    key = 소문자 원소기호(co/ni/li/pb), value = 재활용 함량(%).
    B(저장)·C(검증)가 동일 클래스를 공유해 key 불일치를 구조적으로 차단한다.
    """
    co: Optional[float] = None   # 코발트 함량 %
    ni: Optional[float] = None   # 니켈 함량 %
    li: Optional[float] = None   # 리튬 함량 %
    pb: Optional[float] = None   # 납 함량 %

    @field_validator("co", "ni", "li", "pb")
    @classmethod
    def _pct_range(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("재활용 함량은 0~100% 범위여야 합니다")
        return v

    model_config = {"extra": "forbid"}   # 정의 안 된 key 저장 시 에러 → 오타·오용 즉시 발견