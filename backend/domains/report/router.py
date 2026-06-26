import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db
from backend.infrastructure.pagination import set_total_count
from backend.domains.report.repository import ReportRepository
from backend.domains.report.service import ReportService

router = APIRouter(prefix="/reports", tags=["Report"])


# ── 요청 모델 ────────────────────────────────────────────────────

class CreateReportRequest(BaseModel):
    title: str
    type: str = "compliance"
    related_batch: Optional[uuid.UUID] = None
    summary: Optional[str] = None
    approver_ids: List[uuid.UUID]


class StatusRequest(BaseModel):
    status: str  # "submitted" 만 유효


class ApproveRequest(BaseModel):
    comment: Optional[str] = None


class RejectRequest(BaseModel):
    comment: str  # 반려 사유 필수


# ── 의존성 ──────────────────────────────────────────────────────

def _get_service(db: AsyncSession = Depends(get_db)) -> ReportService:
    return ReportService(ReportRepository(db))


# ── 3.2a GET /reports ────────────────────────────────────────────

@router.get("")
async def list_reports(
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    service: ReportService = Depends(_get_service),
):
    items = await service.list_reports(current_user.tenant_id, page, size)
    total = await service.count_reports(current_user.tenant_id)
    set_total_count(response, total)
    return items


# ── 3.3a GET /reports/inbox  (/{reportId} 보다 먼저 선언해야 라우팅 충돌 없음) ──

@router.get("/inbox")
async def get_inbox(
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    service: ReportService = Depends(_get_service),
):
    items = await service.get_inbox(current_user.user_id)
    set_total_count(response, len(items))
    return items


# ── 3.2b GET /reports/{reportId} ─────────────────────────────────

@router.get("/{report_id}")
async def get_report(
    report_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ReportService = Depends(_get_service),
):
    try:
        return await service.get_report_detail(report_id, current_user.tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")


# ── 3.2c POST /reports ───────────────────────────────────────────

@router.post("", status_code=201)
async def create_report(
    body: CreateReportRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: ReportService = Depends(_get_service),
):
    try:
        report = await service.create_report(
            requester_id=current_user.user_id,
            title=body.title,
            type=body.type,
            batch_id=body.related_batch,
            summary=body.summary,
            approver_ids=body.approver_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"report_id": report.report_id}


# ── 3.2d PATCH /reports/{reportId}/status ────────────────────────

@router.patch("/{report_id}/status")
async def update_report_status(
    report_id: uuid.UUID,
    body: StatusRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: ReportService = Depends(_get_service),
):
    if body.status != "submitted":
        raise HTTPException(status_code=400, detail="status 는 'submitted' 만 허용됩니다.")
    try:
        report = await service.submit(
            report_id=report_id,
            actor_id=current_user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"report_id": report.report_id, "status": report.status}


# ── 3.3b PATCH /reports/{reportId}/approve ───────────────────────

@router.patch("/{report_id}/approve")
async def approve_report(
    report_id: uuid.UUID,
    body: ApproveRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: ReportService = Depends(_get_service),
):
    try:
        report = await service.approve(
            report_id=report_id,
            actor_id=current_user.user_id,
            comment=body.comment,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"report_id": report.report_id, "status": report.status}


# ── 3.3c PATCH /reports/{reportId}/reject ────────────────────────

@router.patch("/{report_id}/reject")
async def reject_report(
    report_id: uuid.UUID,
    body: RejectRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: ReportService = Depends(_get_service),
):
    try:
        report = await service.reject(
            report_id=report_id,
            actor_id=current_user.user_id,
            comment=body.comment,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"report_id": report.report_id, "status": report.status}
