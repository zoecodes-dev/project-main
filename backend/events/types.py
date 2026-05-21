"""
events/types.py  ← 이 파일만 팀 전체가 공유 (스펙 7장 계약)

도메인 간 직접 import 금지. 통신은 이 이벤트 타입 + event_bus.publish()로만.
각자 자기 이벤트 타입을 여기에 추가한다. (28종 자리)

payload는 JSON 직렬화 가능해야 하며, event_bus.publish(event_name, payload)에
넣을 dict는 dataclasses.asdict()로 변환해 전달한다.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import UUID


# ============================================================
# Product (C)
# ============================================================
@dataclass
class ProductCreatedEvent:
    product_id: Optional[UUID] = None
    event_name: str = "ProductCreated"
    occurred_at: Optional[datetime] = None


@dataclass
class LotCreatedEvent:
    lot_id: Optional[UUID] = None
    product_id: Optional[UUID] = None
    event_name: str = "LotCreated"
    occurred_at: Optional[datetime] = None


@dataclass
class BOMMappedEvent:
    product_id: Optional[UUID] = None
    bom_version_id: Optional[UUID] = None
    event_name: str = "BOMMapped"
    occurred_at: Optional[datetime] = None


# ============================================================
# Supplier (B)
# ============================================================
@dataclass
class SupplierInvitedEvent:
    supplier_id: Optional[UUID] = None
    email: Optional[str] = None
    sla_due_date: Optional[datetime] = None
    event_name: str = "SupplierInvited"
    occurred_at: Optional[datetime] = None


@dataclass
class SupplierConnectedEvent:
    supplier_id: Optional[UUID] = None
    tier: Optional[int] = None
    event_name: str = "SupplierConnected"
    occurred_at: Optional[datetime] = None


@dataclass
class SupplierStatusChangedEvent:
    supplier_id: Optional[UUID] = None
    new_status: Optional[str] = None
    event_name: str = "SupplierStatusChanged"
    occurred_at: Optional[datetime] = None


# ============================================================
# Submission (E)
# ============================================================
@dataclass
class SubmissionRequestedEvent:
    request_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    due_date: Optional[datetime] = None
    event_name: str = "SubmissionRequested"
    occurred_at: Optional[datetime] = None


@dataclass
class SubmissionCompletedEvent:
    request_id: Optional[UUID] = None
    batch_id: Optional[UUID] = None
    file_urls: list = field(default_factory=list)
    event_name: str = "SubmissionCompleted"
    occurred_at: Optional[datetime] = None


@dataclass
class SubmissionRejectedEvent:
    request_id: Optional[UUID] = None
    reason: Optional[str] = None
    event_name: str = "SubmissionRejected"
    occurred_at: Optional[datetime] = None


@dataclass
class SubmissionApprovedEvent:
    request_id: Optional[UUID] = None
    batch_id: Optional[UUID] = None
    event_name: str = "SubmissionApproved"
    occurred_at: Optional[datetime] = None


# ============================================================
# Verification (E)
# ============================================================
@dataclass
class ValidationStartedEvent:
    batch_id: Optional[UUID] = None
    rules_applied: list = field(default_factory=list)
    event_name: str = "ValidationStarted"
    occurred_at: Optional[datetime] = None


@dataclass
class ValidationFailedEvent:
    batch_id: Optional[UUID] = None
    violated_rules: list = field(default_factory=list)
    event_name: str = "ValidationFailed"
    occurred_at: Optional[datetime] = None


@dataclass
class ValidationCompletedEvent:
    batch_id: Optional[UUID] = None
    results: list = field(default_factory=list)
    event_name: str = "ValidationCompleted"
    occurred_at: Optional[datetime] = None


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
    occurred_at: Optional[datetime] = None


# ============================================================
# Risk (E)
# ============================================================
@dataclass
class RiskDetectedEvent:
    batch_id: Optional[UUID] = None
    risk_score: Optional[float] = None
    event_name: str = "RiskDetected"
    occurred_at: Optional[datetime] = None


@dataclass
class RiskEscalatedEvent:
    batch_id: Optional[UUID] = None
    reason: Optional[str] = None
    event_name: str = "RiskEscalated"
    occurred_at: Optional[datetime] = None


@dataclass
class RiskResolvedEvent:
    batch_id: Optional[UUID] = None
    resolved_by: Optional[UUID] = None
    event_name: str = "RiskResolved"
    occurred_at: Optional[datetime] = None


# ============================================================
# Compliance (C)
# ============================================================
@dataclass
class ComplianceCompletedEvent:
    batch_id: Optional[UUID] = None
    verdicts: dict = field(default_factory=dict)
    event_name: str = "ComplianceCompleted"
    occurred_at: Optional[datetime] = None


# ============================================================
# HITL (A)
# ============================================================
@dataclass
class HITLRequestedEvent:
    batch_id: Optional[UUID] = None
    reason: Optional[str] = None
    reviewer_id: Optional[UUID] = None
    event_name: str = "HITLRequested"
    occurred_at: Optional[datetime] = None


# ============================================================
# DPP (E)
# ============================================================
@dataclass
class DPPReadinessUpdatedEvent:
    product_id: Optional[UUID] = None
    readiness_score: Optional[float] = None
    event_name: str = "DPPReadinessUpdated"
    occurred_at: Optional[datetime] = None


@dataclass
class DPPIssuedEvent:
    dpp_id: Optional[UUID] = None
    product_id: Optional[UUID] = None
    qr_code_url: Optional[str] = None
    event_name: str = "DPPIssued"
    occurred_at: Optional[datetime] = None
