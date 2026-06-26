"""
domains/due_diligence/models.py

Due Diligence 도메인 Pydantic 요청/응답 스키마.
응답은 snake_case — 프론트 snakeToCamel이 camelCase 변환(CLAUDE.md §2).
"""
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


# ── 5.1 목록 항목 ──────────────────────────────────────────────────────────
class AuditListItem(BaseModel):
    audit_id: UUID
    supplier_id: Optional[UUID]
    supplier_name: Optional[str]
    factory_id: Optional[UUID]
    type: Optional[str]
    status: Optional[str]
    result: Optional[str]
    score: Optional[float]
    risk_score: Optional[float]
    capa_count: int
    has_report: bool

    class Config:
        from_attributes = True


# ── 5.2 상세 추가 필드 ────────────────────────────────────────────────────
class FindingItem(BaseModel):
    title: Optional[str]
    severity: Optional[str]
    description: Optional[str]


class CapaItem(BaseModel):
    capa_id: Optional[str]
    title: Optional[str]
    status: Optional[str]
    due_date: Optional[str]


class AuditDetailResponse(AuditListItem):
    scope: Optional[str]
    agency: Optional[str]
    completed_at: Optional[str]
    findings: List[FindingItem] = []
    capa: List[CapaItem] = []
    report_file_id: Optional[UUID]


# ── 5.3 생성 요청 ──────────────────────────────────────────────────────────
class AuditCreateRequest(BaseModel):
    supplier_id: Optional[UUID] = None
    factory_id: Optional[UUID] = None
    name: str
    scope: str


class AuditCreateResponse(BaseModel):
    audit_id: UUID


# ── 5.4 보고서 업로드 후 결과 ──────────────────────────────────────────────
class AuditReportResponse(BaseModel):
    audit_id: UUID
    result: Optional[str]
    score: Optional[float]
    report_file_id: Optional[UUID]


# ── 5.5 CAPA 상태 갱신 요청 ───────────────────────────────────────────────
class CapaUpdateRequest(BaseModel):
    status: str
