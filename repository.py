from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any

class SupplyChainRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_n_tier_supply_chain(self, root_supplier_id: str) -> List[Dict[str, Any]]:
        """
        특정 공급사(root_supplier_id)를 기점으로 하위 N차 공급망을 재귀적으로 탐색함.
        순환 참조(Cycle) 방지 로직이 포함되어 있음.
        """
        query = text("""
            WITH RECURSIVE supply_tree AS (
                SELECT
                    scm.parent_supplier_id, scm.child_supplier_id, s.company_name, s.tier, 1 AS depth,
                    ARRAY[scm.parent_supplier_id, scm.child_supplier_id] AS path, FALSE AS is_cycle
                FROM supply_chain_map scm
                JOIN suppliers s ON scm.child_supplier_id = s.supplier_id
                WHERE scm.parent_supplier_id = :root_id
                
                UNION ALL
                
                SELECT
                    scm.parent_supplier_id, scm.child_supplier_id, s.company_name, s.tier, t.depth + 1,
                    t.path || scm.child_supplier_id, scm.child_supplier_id = ANY(t.path)
                FROM supply_chain_map scm
                JOIN suppliers s ON scm.child_supplier_id = s.supplier_id
                JOIN supply_tree t ON scm.parent_supplier_id = t.child_supplier_id
                WHERE NOT t.is_cycle
            )
            SELECT depth, tier, company_name, is_cycle 
            FROM supply_tree 
            ORDER BY depth, tier;
        """)
        result = await self.session.execute(query, {"root_id": root_supplier_id})
        return [dict(row._mapping) for row in result]

    async def check_geo_audit_risk_zone(self, lon: float = 87.6271, lat: float = 43.8256, radius_meters: int = 500000) -> List[Dict[str, Any]]:
        """
        협력사 공장/광산 좌표가 지정된 기준 좌표(기본값: 신장 위구르) 반경 내에 존재하는지 검증함.
        """
        query = text("""
            SELECT
                s.supplier_id, s.company_name, sf.factory_id, sf.factory_name, sf.country,
                ST_AsText(sf.location) AS coordinates,
                ST_DWithin(sf.location::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :radius) AS is_in_risk_zone
            FROM supplier_factories sf
            JOIN suppliers s ON sf.supplier_id = s.supplier_id
            WHERE sf.location IS NOT NULL;
        """)
        result = await self.session.execute(query, {"lon": lon, "lat": lat, "radius": radius_meters})
        return [dict(row._mapping) for row in result]