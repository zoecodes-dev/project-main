import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db
from backend.domains.report.repository import ReportRepository
from backend.domains.report.service import ReportService

router = APIRouter(prefix="/report", tags=["Report"])


# ---------- Pydantic 요청 모델 ----------

class CreateReportRequest(BaseModel):
    title: str
    description: Optional[str] = None
    batch_id: Optional[uuid.UUID] = None


class DecisionRequest(BaseModel):
    decision_text: str


# ---------- 의존성 ----------

def _get_service(db: AsyncSession = Depends(get_db)) -> ReportService:
    return ReportService(ReportRepository(db))


# ---------- 엔드포인트 ----------

@router.post("")
async def create_report(
    body: CreateReportRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: ReportService = Depends(_get_service),
    db: AsyncSession = Depends(get_db),
):
    """보고서 초안 생성. manager_id 체인으로 결재선 자동 구성."""
    try:
        report = await service.create_report(
            requester_id=current_user.user_id,
            title=body.title,
            description=body.description,
            batch_id=body.batch_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return {"report_id": report.report_id, "status": report.status}


@router.post("/{report_id}/submit")
async def submit_report(
    report_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ReportService = Depends(_get_service),
    db: AsyncSession = Depends(get_db),
):
    """draft → approval_pending. 1단계 결재자에게 알림."""
    try:
        report = await service.submit(db, report_id=report_id, actor_id=current_user.user_id)
        await db.commit()
        return {"report_id": report.report_id, "status": report.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{report_id}/approve")
async def approve_report(
    report_id: uuid.UUID,
    body: DecisionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: ReportService = Depends(_get_service),
    db: AsyncSession = Depends(get_db),
):
    """현재 단계 승인. 마지막이면 fully_approved, 아니면 다음 단계로."""
    try:
        report = await service.approve(
            db,
            report_id=report_id,
            actor_id=current_user.user_id,
            decision_text=body.decision_text,
        )
        await db.commit()
        return {"report_id": report.report_id, "status": report.status, "current_step": report.current_step}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{report_id}/reject")
async def reject_report(
    report_id: uuid.UUID,
    body: DecisionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: ReportService = Depends(_get_service),
    db: AsyncSession = Depends(get_db),
):
    """현재 단계 반려 → returned."""
    try:
        report = await service.reject(
            db,
            report_id=report_id,
            actor_id=current_user.user_id,
            decision_text=body.decision_text,
        )
        await db.commit()
        return {"report_id": report.report_id, "status": report.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{report_id}/status")
async def get_report_status(
    report_id: uuid.UUID,
    service: ReportService = Depends(_get_service),
    _: CurrentUser = Depends(get_current_user),
):
    """결재선 진행 상태 전체 조회."""
    try:
        return await service.get_status(report_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/inbox")
async def get_inbox(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """현재 로그인 결재자의 대기함 — pending 단계 목록."""
    repo = ReportRepository(db)
    steps = await repo.get_inbox(current_user.user_id)
    return [
        {
            "step_id": s.step_id,
            "report_id": s.report_id,
            "step_number": s.step_number,
            "status": s.status,
        }
        for s in steps
    ]
