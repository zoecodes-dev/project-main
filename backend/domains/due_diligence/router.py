"""
domains/due_diligence/router.py

Due Diligence 도메인 REST 엔드포인트.
prefix: /due-diligence
스펙: 5.1~5.5

인증: 전 엔드포인트 Depends(get_current_user) + tenant_id 격리(CLAUDE.md §4).
커밋: router에서 db.commit() 하지 않음 — service 일원화(CLAUDE.md §1).
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.due_diligence.models import (
    AuditCreateRequest,
    AuditCreateResponse,
    AuditDetailResponse,
    AuditListItem,
    AuditReportResponse,
    CapaUpdateRequest,
)
from backend.domains.due_diligence.repository import DueDiligenceRepository
from backend.domains.due_diligence.service import DueDiligenceService
from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db
from backend.infrastructure.pagination import set_total_count

router = APIRouter(prefix="/due-diligence", tags=["Due Diligence"])


def _get_service(db: AsyncSession = Depends(get_db)) -> DueDiligenceService:
    return DueDiligenceService(DueDiligenceRepository(db))


def _require_tenant(current_user: CurrentUser) -> UUID:
    if current_user.tenant_id is None:
        raise HTTPException(status_code=403, detail="테넌트 정보가 없습니다.")
    return current_user.tenant_id


# ── 5.1  GET /due-diligence ──────────────────────────────────────────────

@router.get("", response_model=List[AuditListItem])
async def list_due_diligence(
    response: Response,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    size: int = 20,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: DueDiligenceService = Depends(_get_service),
):
    """실사 목록 조회. 내 테넌트 소유 기록만. X-Total-Count 헤더 포함."""
    tenant_id = _require_tenant(current_user)
    items, total = await service.list_audits(db, tenant_id, status, search, page, size)
    set_total_count(response, total)
    return items


# ── 5.2  GET /due-diligence/{auditId} ───────────────────────────────────

@router.get("/{audit_id}", response_model=AuditDetailResponse)
async def get_due_diligence(
    audit_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: DueDiligenceService = Depends(_get_service),
):
    """실사 단건 상세 조회. 타 테넌트면 404(존재 은닉)."""
    tenant_id = _require_tenant(current_user)
    detail = await service.get_audit_detail(audit_id, tenant_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return detail


# ── 5.3  POST /due-diligence ────────────────────────────────────────────

@router.post("", response_model=AuditCreateResponse, status_code=201)
async def create_due_diligence(
    body: AuditCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: DueDiligenceService = Depends(_get_service),
):
    """실사 신규 등록. supplier_id/factory_id 선택, name/scope 필수."""
    _require_tenant(current_user)
    result = await service.create_audit(
        db,
        supplier_id=body.supplier_id,
        factory_id=body.factory_id,
        audit_name=body.name,
        audit_scope=body.scope,
    )
    return result


# ── 5.4  PATCH /due-diligence/{auditId}/report  (multipart) ─────────────

@router.patch("/{audit_id}/report", response_model=AuditReportResponse)
async def upload_report(
    audit_id: UUID,
    file: UploadFile = File(...),
    result: Optional[str] = Form(None),
    score: Optional[float] = Form(None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: DueDiligenceService = Depends(_get_service),
):
    """
    보고서 파일 업로드 + result/score 갱신.
    multipart/form-data: field 'file' (binary) + 'result' + 'score' (optional).
    """
    tenant_id = _require_tenant(current_user)
    updated = await service.upload_report(db, audit_id, tenant_id, file, result, score)
    if updated is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return updated


# ── 5.5  PATCH /due-diligence/{auditId}/capa/{capaId} ───────────────────

@router.patch("/{audit_id}/capa/{capa_id}")
async def update_capa_status(
    audit_id: UUID,
    capa_id: str,
    body: CapaUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: DueDiligenceService = Depends(_get_service),
):
    """CAPA 과제 상태 갱신. corrective_actions JSONB 내 해당 capa_id 항목 업데이트."""
    tenant_id = _require_tenant(current_user)
    updated = await service.update_capa_status(db, audit_id, capa_id, body.status, tenant_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Audit or CAPA not found")
    return updated
