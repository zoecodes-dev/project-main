import uuid
from typing import Any, Dict

from backend.agents.state import BatchState
from backend.infrastructure.database import AsyncSessionLocal
from backend.infrastructure.trace import trace_node
from backend.domains.verification.service import verify_feoc_rule
from backend.domains.risk.service import calculate_risk_score
from backend.domains.dpp.service import calculate_readiness
from backend.domains.dpp.service import generate_dpp_payload, create_dpp_record
from backend.domains.dpp.state_machine import issue_dpp


@trace_node("verification", "agent")
async def verification_node(state: BatchState) -> Dict[str, Any]:
    """
    [Verification Engine]
    stage_extraction 완료 후 호출되는 노드예요.
    FEOC 규제 등 Verification 도메인의 룰 엔진을 트리거합니다.
    """
    batch_id = uuid.UUID(state["batch_id"])
    
    # extraction_result에서 검증에 필요한 값들을 추출해요 (없으면 기본값 처리)
    extraction = state.get("extraction_result") or {}
    supplier_id_str = extraction.get("supplier_id")
    
    if not supplier_id_str:
        raise ValueError("extraction_result에 supplier_id가 누락되었습니다.")
    supplier_id = uuid.UUID(supplier_id_str)
    direct_ownership = float(extraction.get("direct_ownership", 0.0))
    indirect_ownership = float(extraction.get("indirect_ownership", 0.0))

    async with AsyncSessionLocal() as db:
        # 도메인 서비스(얇은 래퍼) 호출
        passed = await verify_feoc_rule(
            db=db,
            batch_id=batch_id,
            supplier_id=supplier_id,
            direct_ownership=direct_ownership,
            indirect_ownership=indirect_ownership
        )
        
    return {
        "current_stage": "stage_verification",
        "verification_result": {"feoc_passed": passed}
    }


@trace_node("risk_scoring", "agent")
async def risk_scoring_node(state: BatchState) -> Dict[str, Any]:
    """
    [Pipeline Coordinator]
    stage_compliance 완료 후 호출되는 노드예요.
    다른 노드들이 모아둔 위반 사항을 Risk 도메인으로 넘겨 점수를 계산합니다.
    """
    batch_id = uuid.UUID(state["batch_id"])
    extraction = state.get("extraction_result") or {}
    supplier_id_str = extraction.get("supplier_id")
    
    if not supplier_id_str:
        raise ValueError("extraction_result에 supplier_id가 누락되었습니다.")
    supplier_id = uuid.UUID(supplier_id_str)

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
        risk_result = await calculate_risk_score(
            db=db,
            batch_id=batch_id,
            supplier_id=supplier_id,
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


@trace_node("readiness", "agent")
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


@trace_node("issuance", "agent")
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