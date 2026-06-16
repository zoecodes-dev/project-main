import uuid
from typing import Any, Dict

from sqlalchemy import text

from backend.agents.state import BatchState
from backend.infrastructure.database import AsyncSessionLocal
from backend.domains.verification.service import verify_feoc_rule
from backend.domains.risk.service import calculate_risk_score
from backend.domains.dpp.service import calculate_readiness
from backend.domains.dpp.service import generate_dpp_payload, create_dpp_record
from backend.domains.dpp.state_machine import issue_dpp


async def verification_node(state: BatchState) -> Dict[str, Any]:
    """
    [Verification Engine]
    stage_extraction 완료 후 호출되는 노드예요.
    FEOC 규제 등 Verification 도메인의 룰 엔진을 트리거합니다.
    """
    batch_id = uuid.UUID(state["batch_id"])

    # extraction_result에서 검증 대상 공급사 목록을 추출해요
    # (data_gateway는 N-tier 공급망 전체를 다루므로 단일 supplier_id가 아니라 리스트로 실려와요)
    extraction = state.get("extraction_result") or {}
    supplier_ids = [uuid.UUID(s) for s in (extraction.get("supplier_ids") or [])]

    if not supplier_ids:
        raise ValueError("extraction_result에 supplier_ids가 누락되었습니다.")

    async with AsyncSessionLocal() as db:
        # 지분율은 state가 아니라 supplier_risk_profiles에서 직접 조회해요
        stmt = text("""
            SELECT supplier_id, COALESCE(feoc_direct_ownership, 0), COALESCE(feoc_indirect_ownership, 0)
            FROM supplier_risk_profiles
            WHERE supplier_id = ANY(:sids)
        """)
        rows = (await db.execute(stmt, {"sids": supplier_ids})).fetchall()

        # 체인 내 한 공급사라도 위반이면 배치 전체가 위반 — 위반 전부 기록되도록 끝까지 순회해요
        passed = True
        for supplier_id, direct_ownership, indirect_ownership in rows:
            # 도메인 서비스(얇은 래퍼) 호출
            row_passed = await verify_feoc_rule(
                db=db,
                batch_id=batch_id,
                supplier_id=supplier_id,
                direct_ownership=float(direct_ownership),
                indirect_ownership=float(indirect_ownership)
            )
            if not row_passed:
                passed = False

    return {
        "current_stage": "stage_verification",
        "verification_result": {"feoc_passed": passed}
    }


async def risk_scoring_node(state: BatchState) -> Dict[str, Any]:
    """
    [Pipeline Coordinator]
    stage_compliance 완료 후 호출되는 노드예요.
    다른 노드들이 모아둔 위반 사항을 Risk 도메인으로 넘겨 점수를 계산합니다.
    """
    batch_id = uuid.UUID(state["batch_id"])
    extraction = state.get("extraction_result") or {}
    supplier_ids = [uuid.UUID(s) for s in (extraction.get("supplier_ids") or [])]

    if not supplier_ids:
        raise ValueError("extraction_result에 supplier_ids가 누락되었습니다.")

    # compliance나 geo 등 이전 단계의 위반 내역을 추출해요
    violations = []
    comp_res = state.get("compliance_result") or {}
    if comp_res.get("verdict") in ("compliance_violation", "compliance_reject", "compliance_warning"):
        violations.append({
            "type": comp_res["verdict"],
            "reason": comp_res.get("reasoning_text", "규제 심사 위반")
        })

    geo_res = state.get("geo_result") or {}
    if geo_res.get("risk_detected"):
        violations.append({"type": "GeoRiskDetected", "reason": "지리적 위험(GeoRisk) 검출"})

    async with AsyncSessionLocal() as db:
        # risk 서비스 시그니처는 단수 유지 — 체인 내 FEOC 위반이 검출된 "최악 공급사"를
        # 우선 점수 산정 대상으로 삼고, 위반이 없으면 첫 공급사를 대상으로 해요.
        stmt = text("""
            SELECT supplier_id, COALESCE(feoc_direct_ownership, 0), COALESCE(feoc_indirect_ownership, 0)
            FROM supplier_risk_profiles
            WHERE supplier_id = ANY(:sids)
        """)
        rows = (await db.execute(stmt, {"sids": supplier_ids})).fetchall()

        target_supplier_id = supplier_ids[0]
        worst_total_ownership = -1.0
        for supplier_id, direct_ownership, indirect_ownership in rows:
            direct_ownership = float(direct_ownership)
            indirect_ownership = float(indirect_ownership)
            total_ownership = direct_ownership + indirect_ownership
            is_violation = direct_ownership >= 25.0 or indirect_ownership >= 25.0 or total_ownership >= 25.0
            if is_violation and total_ownership > worst_total_ownership:
                worst_total_ownership = total_ownership
                target_supplier_id = supplier_id

        risk_result = await calculate_risk_score(
            db=db,
            batch_id=batch_id,
            supplier_id=target_supplier_id,
            violations=violations
        )

    is_escalated = risk_result.get("is_escalated", False)

    updates = {
        "current_stage": "stage_risk",
        # 리스크가 70점 이상이면 에스컬레이션(HITL) 플래그를 올려줘요
        "hitl_required": state.get("hitl_required", False) or is_escalated
    }

    # 에스컬레이션 되었다면 사유를 명확히 적어주어 HITL 노드가 헷갈리지 않게 해요
    if is_escalated:
        updates["error_reason"] = "risk_escalated"
        # Supervisor가 hitl_interrupt 노드로 라우팅하도록 점수를 강제로 깎습니다.
        updates["confidence_score"] = 0.84

    return updates


async def readiness_node(state: BatchState) -> Dict[str, Any]:
    """
    [Pipeline Coordinator]
    stage_risk 완료 후 호출되는 노드예요.
    8대 체크리스트 기반으로 Readiness 점수를 계산합니다.
    """
    product_id = uuid.UUID(state["product_id"])

    async with AsyncSessionLocal() as db:
        result = await calculate_readiness(db, product_id)
        score = result["readiness_score"]

        updates = {
            "current_stage": "stage_readiness",
            "readiness_score": score,
        }

        # 만점이 아니라면 보류 사유를 적고, Supervisor가 HITL로 보내도록 유도해요.
        if score < 1.0:
            updates["error_reason"] = "gray_zone"
            updates["confidence_score"] = 0.84

        return updates


async def issuance_node(state: BatchState) -> Dict[str, Any]:
    """
    [Pipeline Coordinator]
    stage_readiness에서 1.0(만점)을 받은 배치를 대상으로 호출되는 노드예요.
    최종 DPP JSON을 생성하고 불변성(Immutable) Lock을 걸어 발행을 확정합니다.
    """
    batch_id = uuid.UUID(state["batch_id"])
    product_id = uuid.UUID(state["product_id"])

    async with AsyncSessionLocal() as db:
        # 1. 80필드 DPP 페이로드 생성
        payload = await generate_dpp_payload(db=db, product_id=product_id, batch_id=batch_id)

        # 2. 탄소발자국 점수 추출 및 DppRecord 초안 생성
        emissions = payload.get("annex_xiii_fields", {}).get("section_4_embedded_emissions_scores", {})
        carbon_footprint = float(emissions.get("59_total_embedded_emissions", 0.0))

        # 3. 서비스 함수를 호출하여 DppRecord 초안을 생성해요 (얇은 래퍼 패턴)
        dpp_id = await create_dpp_record(
            db=db,
            batch_id=batch_id,
            product_id=product_id,
            carbon_footprint=carbon_footprint,
            qr_code_url=f"https://dpp.kira.compliance/verify/{batch_id}",
            payload=payload
        )

        # 4. 발행 처리 및 불변 Lock 확정
        await issue_dpp(db=db, dpp_id=dpp_id)

    return {
        "current_stage": "stage_issuance",
        "batch_status": "batch_completed"
    }
