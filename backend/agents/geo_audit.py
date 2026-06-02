"""
agents/geo_audit.py  (담당: 팀원 D · 영수)

Geo Audit Agent. 공장·광산 좌표 진위성 검사 + 고위험 지역 판정.
스펙 5-2 기준. W1은 시그니처 + 깡통, 실제 PostGIS 공간 쿼리는 W3.
"""
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.state import BatchState
from backend.infrastructure.trace import trace_node, trace_tool
from backend.domains.supplychain.repository import SupplyChainRepository
from backend.domains.supplychain.service import SupplyChainService

# 신장 위구르 자치구 경계 (SRID 4326)
XINJIANG_REGION_WKT = (
    "POLYGON((73.4 34.8, 96.4 34.8, 96.4 49.2, 73.4 49.2, 73.4 34.8))"
)


@trace_tool("xinjiang_proximity_check")
async def check_xinjiang_proximity(location_wkt: str, db: AsyncSession) -> dict:
    """
    ST_DWithin으로 신장 경계 내부 또는 50km 이내 여부 판정.
    반환: {"is_high_risk": bool, "distance_km": float, "region": "xinjiang" | None}
    실제 PostGIS 공간 쿼리로 교체됨.
    """
    query = text("""
        SELECT 
            ST_DWithin(
                ST_GeomFromText(:loc_wkt, 4326)::geography,
                ST_GeomFromText(:xj_wkt, 4326)::geography,
                50000
            ) AS is_high_risk,
            ST_Distance(
                ST_GeomFromText(:loc_wkt, 4326)::geography,
                ST_GeomFromText(:xj_wkt, 4326)::geography
            ) / 1000.0 AS distance_km
    """)
    result = await db.execute(query, {
        "loc_wkt": location_wkt,
        "xj_wkt": XINJIANG_REGION_WKT
    })
    row = result.first()
    
    is_high_risk = bool(row.is_high_risk) if row else False
    distance_km = float(row.distance_km) if row and row.distance_km is not None else None

    return {
        "is_high_risk": is_high_risk,
        "distance_km": distance_km,
        "region": "xinjiang" if is_high_risk else None
    }


@trace_tool("eudr_deforestation_check")
async def check_eudr_deforestation(location_wkt: str, db: AsyncSession) -> dict:
    """산림 훼손 고위험 좌표 여부. 초기엔 외부 검색 기반. W1: 깡통."""
    return {"is_high_risk": False, "source": None}


@trace_tool("coordinate_authenticity")
async def verify_coordinates_authenticity(
    factory_id: UUID, db: AsyncSession
) -> dict:
    """
    supplier_factories.location과 country 불일치 시 risk_level=high.
    W1: 깡통. W3에서 ST_Within 국가 경계 대조.
    """
    return {"factory_id": str(factory_id), "country_match": True, "risk_level": "low"}


@trace_node("geo_audit", "agent")
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
            "high_risk_factories": detected_risks
        },
        "current_stage": "stage_geo",
    }
