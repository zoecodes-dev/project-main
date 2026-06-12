"""
agents/geo_audit.py  (담당: 팀원 D · 영수)

Geo Audit Agent. 공장·광산 좌표 진위성 검사 + 고위험 지역 판정.
스펙 5-2 기준. W1은 시그니처 + 깡통, 실제 PostGIS 공간 쿼리는 W3.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.state import BatchState
from backend.domains.supplychain.repository import SupplyChainRepository
from backend.domains.supplychain.service import SupplyChainService

async def geo_audit_node(state: BatchState, db: AsyncSession) -> BatchState:
    """
    배치의 모든 공장 좌표를 검사하고 결과를 state에 기록.
    고위험 발견 시 GeoRiskDetected 발행은 service 계층에 위임.
    """
    batch_id = state.get("batch_id")
    
    repo = SupplyChainRepository(db)
    service = SupplyChainService(repo)
    
    # 실제 좌표 루프 및 고위험 지역 검사 수행 (리스크 큐 적재 포함)
    detected_risks = await service.execute_geo_audit(db, batch_id=batch_id)
    
    # Geo Audit 고위험 감지 시 confidence_score 인터럽트 연동
    confidence_score = state.get("confidence_score", 1.0)
    error_reason = state.get("error_reason", None)
    hitl_required = state.get("hitl_required", False)
    
    if detected_risks:
        # 0.84 이하로 깎아서 Supervisor가 hitl_interrupt로 즉시 분기하도록 유도
        confidence_score = min(float(confidence_score) if confidence_score else 1.0, 0.84)  
        error_reason = "geographical_risk"
        hitl_required = True

    return {
        **state,
        "geo_result": {
            "risk_detected": detected_risks
        },
        "confidence_score": confidence_score,
        "error_reason": error_reason,
        "hitl_required": hitl_required,
        "current_stage": "stage_geo",
    }
