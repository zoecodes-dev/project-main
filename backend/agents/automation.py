"""
agents/automation.py  (담당: 팀원 E 차윤)

W2 D2+D3: 4개 automation 노드를 그래프에서 제거하고 결정론 후처리 서비스 함수로 재배치.

  변경 전: graph에 verification / risk_scoring / readiness / issuance 노드로 등록
  변경 후: compliance 완료 후 run_post_compliance_pipeline() 후처리로 호출
           (A1 핸들러 — agents/graph.py — 의 compliance 완료 콜백에서 invoke)
"""
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.state import BatchState
from backend.infrastructure.database import AsyncSessionLocal
from backend.domains.verification.service import verify_feoc_rule
from backend.domains.risk.service import calculate_risk_score
from backend.domains.dpp.service import calculate_readiness, generate_dpp_payload, create_dpp_record
from backend.domains.dpp.state_machine import issue_dpp
from backend.domains.product.models import Product as _Product  # noqa: F401 — SA 매퍼 등록


# ── 1. FEOC 지분 검증 ──────────────────────────────────────────────────────
async def run_feoc_verification(
    db: AsyncSession,
    batch_id: uuid.UUID,
    supplier_ids: List[uuid.UUID],
) -> Dict[str, Any]:
    """
    FEOC 지분 규칙 검증. compliance judge_ira 내 흡수 예정; 과도기에는 직접 호출 가능.
    체인 내 공급사 중 한 곳이라도 위반이면 feoc_passed=False.
    """
    stmt = text("""
        SELECT supplier_id, COALESCE(feoc_direct_ownership, 0), COALESCE(feoc_indirect_ownership, 0)
        FROM supplier_risk_profiles
        WHERE supplier_id = ANY(:sids)
    """)
    rows = (await db.execute(stmt, {"sids": supplier_ids})).fetchall()

    passed = True
    for supplier_id, direct_ownership, indirect_ownership in rows:
        row_passed = await verify_feoc_rule(
            db=db,
            batch_id=batch_id,
            supplier_id=supplier_id,
            direct_ownership=float(direct_ownership),
            indirect_ownership=float(indirect_ownership),
        )
        if not row_passed:
            passed = False

    return {"feoc_passed": passed}


# ── 2. 위험 점수 산정 ──────────────────────────────────────────────────────
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
    반환값의 hitl_required=True 이면 run_post_compliance_pipeline이 즉시 early return하여
    supervisor가 hitl_interrupt 노드로 라우팅한다.
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


# ── 3. DPP 준비도 체크 ────────────────────────────────────────────────────
async def run_readiness(
    db: AsyncSession,
    product_id: uuid.UUID,
) -> Dict[str, Any]:
    """
    8대 체크리스트 기반 Readiness 점수 산정.
    만점(1.0) 미만이면 gray_zone 플래그 → supervisor가 hitl_interrupt로 라우팅.
    """
    result = await calculate_readiness(db, product_id)
    score = result["readiness_score"]

    updates: Dict[str, Any] = {
        "current_stage": "stage_readiness",
        "readiness_score": score,
    }
    if score < 1.0:
        updates["error_reason"] = "gray_zone"

    return updates


# ── 4. DPP 발행 ───────────────────────────────────────────────────────────
async def run_issuance(
    db: AsyncSession,
    batch_id: uuid.UUID,
    product_id: uuid.UUID,
) -> Dict[str, Any]:
    """
    DPP JSON 생성 + 불변 Lock 확정 발행.
    """
    payload = await generate_dpp_payload(db=db, product_id=product_id, batch_id=batch_id)

    emissions = payload.get("annex_xiii_fields", {}).get("section_4_embedded_emissions_scores", {})
    carbon_footprint = float(emissions.get("59_total_embedded_emissions", 0.0))

    dpp_id = await create_dpp_record(
        db=db,
        batch_id=batch_id,
        product_id=product_id,
        carbon_footprint=carbon_footprint,
        qr_code_url=f"https://dpp.kira.compliance/verify/{batch_id}",
        payload=payload,
    )
    await issue_dpp(db=db, dpp_id=dpp_id)

    return {"current_stage": "stage_issuance", "batch_status": "batch_completed"}


# ── 후처리 파이프라인 (compliance 완료 후 A1 핸들러가 호출) ───────────────
async def run_post_compliance_pipeline(state: BatchState) -> Dict[str, Any]:
    """
    compliance 완료 직후 A1 핸들러(agents/graph.py)가 호출하는 결정론 후처리 파이프라인.
    risk_scoring → readiness → issuance 를 단일 DB 세션에서 순차 실행한다.

    HITL 조건 발생 시 즉시 partial 업데이트를 반환하여
    supervisor가 hitl_interrupt 노드로 라우팅할 수 있게 한다.
    """
    batch_id = uuid.UUID(state["batch_id"])
    product_id = uuid.UUID(state["product_id"])
    extraction = state.get("extraction_result") or {}
    supplier_ids = [uuid.UUID(s) for s in (extraction.get("supplier_ids") or [])]

    if not supplier_ids:
        raise ValueError("extraction_result에 supplier_ids가 누락되었습니다.")

    async with AsyncSessionLocal() as db:
        # 1) 위험 점수 산정
        risk_updates = await run_risk_scoring(
            db=db,
            batch_id=batch_id,
            supplier_ids=supplier_ids,
            compliance_result=state.get("compliance_result"),
            geo_result=state.get("geo_result"),
            current_hitl_required=bool(state.get("hitl_required")),
        )
        if risk_updates.get("hitl_required"):
            return risk_updates

        # 2) DPP 준비도 체크
        readiness_updates = await run_readiness(db=db, product_id=product_id)
        if readiness_updates.get("error_reason") == "gray_zone":
            return readiness_updates

        # 3) DPP 발행
        issuance_updates = await run_issuance(db=db, batch_id=batch_id, product_id=product_id)

    return {**risk_updates, **readiness_updates, **issuance_updates}
