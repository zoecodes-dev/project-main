import uuid
from typing import Any, Dict

from backend.agents.state import BatchState
from backend.infrastructure.database import get_db
from backend.infrastructure.trace import trace_node
from backend.domains.verification.service import verify_feoc_rule
from backend.domains.risk.service import calculate_risk_score
from backend.domains.dpp.service import calculate_readiness
from backend.domains.dpp.service import generate_dpp_payload
from backend.domains.dpp.state_machine import issue_dpp
from backend.domains.dpp.models import DppRecord


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
    supplier_id = uuid.UUID(supplier_id_str) if supplier_id_str else uuid.uuid4()
    direct_ownership = float(extraction.get("direct_ownership", 0.0))
    indirect_ownership = float(extraction.get("indirect_ownership", 0.0))

    async for db in get_db():
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
    supplier_id = uuid.UUID(supplier_id_str) if supplier_id_str else uuid.uuid4()

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

    async for db in get_db():
        risk_result = await calculate_risk_score(
            db=db,
            batch_id=batch_id,
            supplier_id=supplier_id,
            violations=violations
        )
        
    return {
        "current_stage": "stage_risk",
        # 리스크가 70점 이상이면 에스컬레이션(HITL) 플래그를 올려줘요
        "hitl_required": state.get("hitl_required", False) or risk_result.get("is_escalated", False)
    }


@trace_node("readiness", "agent")
async def readiness_node(state: BatchState) -> Dict[str, Any]:
    """
    [Pipeline Coordinator]
    stage_risk 완료 후 호출되는 노드예요.
    8대 체크리스트 기반으로 Readiness 점수를 계산합니다.
    """
    product_id = uuid.UUID(state["product_id"])
    
    async for db in get_db():
        result = await calculate_readiness(db, product_id)
        score = result["readiness_score"]
        
        # Readiness가 1.0(만점) 미만이면 사람의 확인(HITL)이 필요하도록 상태를 바꿔요
        batch_status = "batch_completed" if score >= 1.0 else "batch_hitl_wait"
        
        return {
            "current_stage": "stage_readiness",
            "readiness_score": score,
            "batch_status": batch_status
        }


@trace_node("issuance", "agent")
async def issuance_node(state: BatchState) -> Dict[str, Any]:
    """
    [Pipeline Coordinator]
    stage_readiness에서 1.0(만점)을 받은 배치를 대상으로 호출되는 노드예요.
    최종 DPP JSON을 생성하고 불변성(Immutable) Lock을 걸어 발행을 확정합니다.
    """
    batch_id = uuid.UUID(state["batch_id"])
    product_id = uuid.UUID(state["product_id"])
    
    async for db in get_db():
        # 1. 80필드 DPP 페이로드 생성
        payload = await generate_dpp_payload(db=db, product_id=product_id, batch_id=batch_id)
        
        # 2. 탄소발자국 점수 추출 및 DppRecord 초안 생성
        emissions = payload.get("annex_xiii_fields", {}).get("section_4_embedded_emissions_scores", {})
        carbon_footprint = float(emissions.get("59_total_embedded_emissions", 0.0))
        
        # DB의 DEFAULT 'dpp_issued' 제약에 걸리지 않도록 status=None을 명시해 줘요.
        # 그래야 assert_not_issued 가드를 무사히 통과하고 issue_dpp에서 확정 지을 수 있어요.
        dpp_record = DppRecord(
            batch_id=batch_id,
            product_id=product_id,
            carbon_footprint=carbon_footprint,
            qr_code_url=f"https://dpp.kira.compliance/verify/{batch_id}",
            payload=payload,
            status=None
        )
        db.add(dpp_record)
        await db.flush()  # dpp_id를 발급받기 위해 flush
        
        # 3. 발행 처리 및 불변 Lock 확정
        await issue_dpp(db=db, dpp_id=dpp_record.dpp_id)
        
    return {
        "current_stage": "stage_issuance",
        "batch_status": "batch_completed"
    }