from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import get_db
from backend.domains.batches.repository import list_batches_by_status, get_dashboard_kpis

batches_router = APIRouter(prefix="/batches", tags=["Batches"])
dashboard_router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@batches_router.get("")
async def get_batches(
    status: Literal["processing", "hitl_wait", "completed", "rejected"] = Query(
        "processing",
        description="배치 상태 필터 (processing | hitl_wait | completed | rejected)",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    BE-1: GET /batches?status=processing

    지정된 상태의 배치 목록을 단계(current_stage)별로 그룹핑해 반환합니다.
    기본값은 처리 중(processing)입니다.

    Response:
        status     — 조회된 DB 상태값
        total      — 해당 상태 배치 전체 수
        by_stage   — { stage_name: [batch, ...] } 단계별 배치 목록
    """
    return await list_batches_by_status(db, status)


@dashboard_router.get("/kpis")
async def get_kpis(db: AsyncSession = Depends(get_db)):
    """
    BE-2: GET /dashboard/kpis

    batches / dpp_records / compliance_results 3개 테이블에서
    대시보드 집계 8종을 반환합니다.

    KPIs:
        1. total_batches           전체 배치 수
        2. processing_batches      처리 중 배치 수
        3. hitl_wait_batches       HITL 대기 배치 수
        4. completed_batches       완료 배치 수
        5. rejected_batches        거부 배치 수
        6. dpp_issued_count        발행 DPP 수
        7. compliance_pass_rate    규제 통과율(%)
        8. avg_confidence_score    평균 신뢰도 점수
    """
    return await get_dashboard_kpis(db)
