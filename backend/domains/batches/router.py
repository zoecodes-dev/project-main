from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import get_db
from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.domains.batches.repository import (
    list_batches_by_status,
    get_dashboard_kpis,
    get_batch_detail,
)

batches_router = APIRouter(prefix="/batches", tags=["Batches"])
dashboard_router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@batches_router.get("")
async def get_batches(
    status: Literal["processing", "hitl_wait", "completed", "rejected"] = Query(
        "processing",
        description="배치 상태 필터 (processing | hitl_wait | completed | rejected)",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    BE-1: GET /batches?status=processing

    지정된 상태의 배치 목록을 단계(current_stage)별로 그룹핑해 반환합니다.
    기본값은 처리 중(processing)입니다. 내 테넌트 배치만(§0.2).

    Response:
        status     — 조회된 DB 상태값
        total      — 해당 상태 배치 전체 수
        by_stage   — { stage_name: [batch, ...] } 단계별 배치 목록
    """
    return await list_batches_by_status(db, status, current_user.tenant_id)


@batches_router.get("/{batch_id}")
async def get_batch(
    batch_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    BE-3: GET /batches/{batch_id}

    배치 상세 조회 — 5종 판정 결과 포함.

    Response:
        batch_id, product_id, destination, current_stage, status,
        confidence_score, readiness_score, received_at
        compliance_result  — {verdicts, needs_human_review, details[]}
        geo_result         — {risk_detected, risk_flags, detected_risks[]}
        verification_result— {feoc_passed, violations[]}  (E R4 완료 전 null)
        risk_result        — {max_risk_score, has_high_risk}
    """
    result = await get_batch_detail(db, str(batch_id), current_user.tenant_id)
    if result is None:
        raise HTTPException(status_code=404, detail="배치를 찾을 수 없습니다.")
    return result


@dashboard_router.get("/kpis")
async def get_kpis(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    BE-2: GET /dashboard/kpis

    batches / compliance_results 2개 테이블에서
    대시보드 집계 7종을 반환합니다.

    KPIs:
        1. total_batches           전체 배치 수
        2. processing_batches      처리 중 배치 수
        3. hitl_wait_batches       HITL 대기 배치 수
        4. completed_batches       완료 배치 수
        5. rejected_batches        거부 배치 수
        6. compliance_pass_rate    규제 통과율(%)
        7. avg_confidence_score    평균 신뢰도 점수
    (내 테넌트로 격리 — §0.2)
    """
    return await get_dashboard_kpis(db, current_user.tenant_id)


@dashboard_router.get("/supplier-stats")
async def get_dashboard_supplier_stats(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    대시보드 협력사 집계 — suppliers 테이블에서 1쿼리로 반환.
      - total_count        전체 협력사 수
      - verified_count     검증 완료(supplier_verified) 수
      - high_risk_count    고위험 이상(high/critical) 수
      - incomplete_count   입력 미완료(completeness_score < 80) 수
      - average_completeness  평균 완성도(%)
    """
    result = await db.execute(
        text("""
            SELECT
                COUNT(*)                                                        AS total_count,
                COUNT(*) FILTER (WHERE status = 'supplier_verified')           AS verified_count,
                COUNT(*) FILTER (WHERE risk_level IN ('high', 'critical'))     AS high_risk_count,
                COUNT(*) FILTER (WHERE completeness_score < 80)                AS incomplete_count,
                COALESCE(ROUND(AVG(completeness_score)), 0)                    AS average_completeness
            FROM suppliers
            WHERE (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = CAST(:tenant_id AS uuid))
        """),
        {"tenant_id": str(current_user.tenant_id) if current_user.tenant_id else None},
    )
    row = result.mappings().one()
    return {
        "total_count":          row["total_count"],
        "verified_count":       row["verified_count"],
        "high_risk_count":      row["high_risk_count"],
        "incomplete_count":     row["incomplete_count"],
        "average_completeness": int(row["average_completeness"]),
    }
