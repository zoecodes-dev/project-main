from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 판정 결과 조회 시 compliance 상세 반환 최대 건수
_COMPLIANCE_DETAIL_LIMIT = 20

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


async def get_batch_detail(
    db: AsyncSession,
    batch_id: str,
) -> Optional[Dict[str, Any]]:
    """
    BE-3: 배치 상세 조회 — compliance·geo·verification·risk·readiness 5종 판정 결과 포함.

    R7 계약:
      - compliance_result : compliance_results 테이블 집계 (규제코드→verdict 맵 + 상세)
      - geo_result        : geo_audit_results 테이블 (D R5 저장)
      - verification_result: verification_results 테이블 (E R4 저장; 미저장 시 null)
      - risk_result       : supplier_risk_profiles에서 배치 공급망 최고 위험도 조회
      - dpp_result        : dpp_records 테이블
    """
    batch_row = (await db.execute(
        text("""
            SELECT
                b.batch_id, b.product_id, b.bom_version_id, b.tenant_id,
                b.destination, b.current_stage, b.status,
                b.confidence_score, b.readiness_score,
                b.received_at, b.source_system, b.external_id
            FROM batches b
            WHERE b.batch_id = :batch_id
        """),
        {"batch_id": batch_id},
    )).mappings().fetchone()

    if batch_row is None:
        return None

    # ── compliance 판정 결과 ─────────────────────────────────────────────────
    compliance_rows = (await db.execute(
        text("""
            SELECT
                r.regulation_code,
                cr.verdict,
                cr.needs_human_review,
                cr.confidence_score,
                cr.reasoning_text
            FROM compliance_results cr
            JOIN regulations r ON r.regulation_id = cr.regulation_id
            WHERE cr.batch_id = :batch_id
            ORDER BY cr.created_at DESC
            LIMIT :lim
        """),
        {"batch_id": batch_id, "lim": _COMPLIANCE_DETAIL_LIMIT},
    )).mappings().fetchall()

    # ── geo 판정 결과 ────────────────────────────────────────────────────────
    geo_row = (await db.execute(
        text("""
            SELECT risk_detected, risk_flags, detected_risks
            FROM geo_audit_results
            WHERE batch_id = :batch_id
        """),
        {"batch_id": batch_id},
    )).mappings().fetchone()

    # ── verification 판정 결과 (E R4 적재 전까지 null) ───────────────────────
    verif_row = (await db.execute(
        text("""
            SELECT feoc_passed, violations
            FROM verification_results
            WHERE batch_id = :batch_id
        """),
        {"batch_id": batch_id},
    )).mappings().fetchone()

    # ── risk 점수 (공급망 공급사 중 최고 위험도) ─────────────────────────────
    risk_row = (await db.execute(
        text("""
            SELECT
                MAX(srp.overall_risk_score)  AS max_risk_score,
                BOOL_OR(srp.is_high_risk_flag) AS has_high_risk
            FROM batches b
            JOIN supply_chain_map scm ON scm.bom_version_id = b.bom_version_id
            JOIN supplier_risk_profiles srp
                ON srp.supplier_id = scm.child_supplier_id
            WHERE b.batch_id = :batch_id
        """),
        {"batch_id": batch_id},
    )).mappings().fetchone()

    # ── DPP 결과 ────────────────────────────────────────────────────────────
    dpp_row = (await db.execute(
        text("""
            SELECT dpp_id, status, issued_at
            FROM dpp_records
            WHERE batch_id = :batch_id
            LIMIT 1
        """),
        {"batch_id": batch_id},
    )).mappings().fetchone()

    # ── 조립 ────────────────────────────────────────────────────────────────
    return {
        "batch_id":        str(batch_row["batch_id"]),
        "product_id":      str(batch_row["product_id"]) if batch_row["product_id"] else None,
        "destination":     batch_row["destination"],
        "current_stage":   batch_row["current_stage"],
        "status":          batch_row["status"],
        "confidence_score": (
            float(batch_row["confidence_score"])
            if batch_row["confidence_score"] is not None else None
        ),
        "readiness_score": (
            float(batch_row["readiness_score"])
            if batch_row["readiness_score"] is not None else None
        ),
        "received_at": (
            batch_row["received_at"].isoformat() if batch_row["received_at"] else None
        ),
        "compliance_result": {
            "verdicts": {r["regulation_code"]: r["verdict"] for r in compliance_rows},
            "needs_human_review": any(r["needs_human_review"] for r in compliance_rows),
            "details": [
                {
                    "regulation_code":    r["regulation_code"],
                    "verdict":            r["verdict"],
                    "needs_human_review": r["needs_human_review"],
                    "confidence_score":   (
                        float(r["confidence_score"])
                        if r["confidence_score"] is not None else None
                    ),
                    "reasoning_text": r["reasoning_text"] or "",
                }
                for r in compliance_rows
            ],
        } if compliance_rows else None,
        "geo_result": {
            "risk_detected": bool(geo_row["risk_detected"]),
            "risk_flags":    geo_row["risk_flags"] or [],
            "detected_risks": geo_row["detected_risks"] or [],
        } if geo_row else None,
        "verification_result": {
            "feoc_passed": bool(verif_row["feoc_passed"]),
            "violations":  verif_row["violations"] or [],
        } if verif_row else None,
        "risk_result": {
            "max_risk_score": (
                int(risk_row["max_risk_score"])
                if risk_row and risk_row["max_risk_score"] is not None else None
            ),
            "has_high_risk": bool(risk_row["has_high_risk"]) if risk_row else False,
        },
        "dpp_result": {
            "dpp_id":    str(dpp_row["dpp_id"]),
            "status":    dpp_row["status"],
            "issued_at": (
                dpp_row["issued_at"].isoformat() if dpp_row["issued_at"] else None
            ),
        } if dpp_row else None,
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


