"""
agents/geo_audit.py  (담당: 팀원 D · 영수)

Geo Audit Agent. 공장·광산 좌표 진위성 검사 + 고위험 지역 판정.
스펙 5-2 기준. W1은 시그니처 + 깡통, 실제 PostGIS 공간 쿼리는 W3.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.state import BatchState
from backend.infrastructure.trace import trace_node
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
    
    return {
        **state,
        "geo_result": {
            "risk_detected": detected_risks
        },
        "current_stage": "stage_geo",
    }
