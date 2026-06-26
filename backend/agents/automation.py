"""
agents/automation.py  (담당: 팀원 E 차윤)

결정론 automation 노드. compliance 완료 후 risk_scoring 노드로 그래프에서 호출된다.
(A1 핸들러 — agents/graph.py — 가 노드로 등록·invoke)
"""
import uuid
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.risk.service import calculate_risk_score
from backend.domains.product.models import Product as _Product  # noqa: F401 — SA 매퍼 등록


# ── 1. 위험 점수 산정 ──────────────────────────────────────────────────────
async def run_risk_scoring(
    db: AsyncSession,
    batch_id: uuid.UUID,
    supplier_ids: List[uuid.UUID],
    compliance_result: Optional[Dict[str, Any]] = None,
    geo_result: Optional[Dict[str, Any]] = None,
    current_hitl_required: bool = False,
) -> Dict[str, Any]:
    """
    compliance·geo 위반 내역을 종합해 위험 점수를 산정하고 에스컬레이션 여부를 반환한다.
    반환값의 hitl_required=True 이면 supervisor가 hitl_interrupt 노드로 라우팅한다.
    """
    violations: List[Dict[str, str]] = []

    comp_res = compliance_result or {}
    if comp_res.get("verdict") in ("compliance_violation", "compliance_reject", "compliance_warning"):
        violations.append({
            "type": comp_res["verdict"],
            "reason": comp_res.get("reasoning_text", "규제 심사 위반"),
        })

    geo_res = geo_result or {}
    if geo_res.get("risk_detected"):
        violations.append({"type": "GeoRiskDetected", "reason": "지리적 위험(GeoRisk) 검출"})

    # FEOC 위반이 가장 심각한 공급사를 우선 점수 산정 대상으로 선정
    stmt = text("""
        SELECT supplier_id, COALESCE(feoc_direct_ownership, 0), COALESCE(feoc_indirect_ownership, 0)
        FROM supplier_risk_profiles
        WHERE supplier_id = ANY(:sids)
    """)
    rows = (await db.execute(stmt, {"sids": supplier_ids})).fetchall()

    target_supplier_id = supplier_ids[0]
    worst_total = -1.0
    for supplier_id, direct, indirect in rows:
        direct, indirect = float(direct), float(indirect)
        total = direct + indirect
        if (direct >= 25.0 or indirect >= 25.0 or total >= 25.0) and total > worst_total:
            worst_total = total
            target_supplier_id = supplier_id

    risk_result = await calculate_risk_score(
        db=db,
        batch_id=batch_id,
        supplier_id=target_supplier_id,
        violations=violations,
    )

    is_escalated = risk_result.get("is_escalated", False)
    updates: Dict[str, Any] = {
        "current_stage": "stage_risk",
        "hitl_required": current_hitl_required or is_escalated,
    }
    if is_escalated:
        updates["error_reason"] = "risk_escalated"

    return updates


