from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# API 단축키 → DB 상태값 매핑
_STATUS_MAP = {
    "processing": "batch_processing",
    "hitl_wait":  "batch_hitl_wait",
    "completed":  "batch_completed",
    "rejected":   "batch_rejected",
}

# DB에 정의된 모든 stage 순서 (current_stage 그룹핑 키 정렬용)
_STAGE_ORDER = [
    "stage_queued", "stage_extraction", "stage_verification",
    "stage_geo", "stage_compliance", "stage_risk",
    "stage_readiness", "stage_issuance",
]

# AgentStage: DB current_stage 값 → 한국어 표시 레이블 + 진행 인덱스(1-base)
AGENT_STAGE_META: Dict[str, Dict[str, Any]] = {
    "stage_queued":       {"label": "대기",         "index": 1},
    "stage_extraction":   {"label": "데이터 추출",   "index": 2},
    "stage_verification": {"label": "검증",          "index": 3},
    "stage_geo":          {"label": "지역 감사",      "index": 4},
    "stage_compliance":   {"label": "컴플라이언스",   "index": 5},
    "stage_risk":         {"label": "리스크 분석",    "index": 6},
    "stage_readiness":    {"label": "발행 준비도",    "index": 7},
    "stage_issuance":     {"label": "DPP 발행",      "index": 8},
}
_TOTAL_STAGES = len(AGENT_STAGE_META)


async def list_batches_by_status(
    db: AsyncSession,
    status: str,
) -> Dict[str, Any]:
    """
    BE-1: 특정 상태 배치 목록을 단계(current_stage)별로 그룹핑해 반환한다.
    status 파라미터는 단축형("processing" 등)을 받아 DB 값으로 변환한다.
    """
    db_status = _STATUS_MAP.get(status, f"batch_{status}")

    rows = (await db.execute(
        text("""
            SELECT
                b.batch_id,
                b.product_id,
                b.tenant_id,
                b.destination,
                b.current_stage,
                b.status,
                b.confidence_score,
                b.received_at,
                b.source_system,
                b.external_id
            FROM batches b
            WHERE b.status = :status
            ORDER BY b.received_at DESC
        """),
        {"status": db_status},
    )).mappings().fetchall()

    by_stage: Dict[str, List[Dict]] = {s: [] for s in _STAGE_ORDER}
    for r in rows:
        stage = r["current_stage"] or "stage_queued"
        entry = {
            "batch_id":        str(r["batch_id"]),
            "product_id":      str(r["product_id"]) if r["product_id"] else None,
            "tenant_id":       str(r["tenant_id"]) if r["tenant_id"] else None,
            "destination":     r["destination"],
            "current_stage":   stage,
            "status":          r["status"],
            "confidence_score": float(r["confidence_score"]) if r["confidence_score"] is not None else None,
            "received_at":     r["received_at"].isoformat() if r["received_at"] else None,
            "source_system":   r["source_system"],
            "external_id":     r["external_id"],
        }
        if stage in by_stage:
            by_stage[stage].append(entry)
        else:
            by_stage.setdefault(stage, []).append(entry)

    # 빈 stage 키 제거
    by_stage = {k: v for k, v in by_stage.items() if v}

    return {
        "status":    db_status,
        "total":     len(rows),
        "by_stage":  by_stage,
    }


async def get_dashboard_kpis(db: AsyncSession) -> Dict[str, Any]:
    """
    BE-2: 대시보드 집계 8종을 batches/dpp_records/compliance_results에서 추출한다.

    KPI 목록:
      1. total_batches           — 전체 배치 수
      2. processing_batches      — 처리 중(batch_processing) 수
      3. hitl_wait_batches       — 사람 검토 대기(batch_hitl_wait) 수
      4. completed_batches       — 완료(batch_completed) 수
      5. rejected_batches        — 거부(batch_rejected) 수
      6. dpp_issued_count        — 발행된 DPP 수
      7. compliance_pass_rate    — 규제 통과율(%) — compliance_passed / 전체
      8. avg_confidence_score    — 배치 평균 신뢰도 점수
    """
    batch_row = (await db.execute(text("""
        SELECT
            COUNT(*)                                                          AS total_batches,
            COUNT(*) FILTER (WHERE status = 'batch_processing')              AS processing_batches,
            COUNT(*) FILTER (WHERE status = 'batch_hitl_wait')               AS hitl_wait_batches,
            COUNT(*) FILTER (WHERE status = 'batch_completed')               AS completed_batches,
            COUNT(*) FILTER (WHERE status = 'batch_rejected')                AS rejected_batches,
            ROUND(AVG(confidence_score)::NUMERIC, 4)                         AS avg_confidence_score
        FROM batches
    """))).mappings().fetchone()

    dpp_row = (await db.execute(text("""
        SELECT COUNT(*) AS dpp_issued_count
        FROM dpp_records
        WHERE status = 'dpp_issued'
    """))).fetchone()

    cr_row = (await db.execute(text("""
        SELECT
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE verdict = 'compliance_passed')
                / NULLIF(COUNT(*), 0),
                2
            ) AS compliance_pass_rate
        FROM compliance_results
    """))).fetchone()

    return {
        "total_batches":        int(batch_row["total_batches"] or 0),
        "processing_batches":   int(batch_row["processing_batches"] or 0),
        "hitl_wait_batches":    int(batch_row["hitl_wait_batches"] or 0),
        "completed_batches":    int(batch_row["completed_batches"] or 0),
        "rejected_batches":     int(batch_row["rejected_batches"] or 0),
        "dpp_issued_count":     int(dpp_row[0] or 0),
        "compliance_pass_rate": float(cr_row[0] or 0.0),
        "avg_confidence_score": float(batch_row["avg_confidence_score"] or 0.0),
    }


