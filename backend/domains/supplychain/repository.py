"""
domains/supplychain/repository.py  (담당: 팀원 D · 영수)

공급망 그래프 데이터 접근 계층. 재귀 CTE 기반 N차 탐색 + PostGIS Geo Audit.
모든 주요 쿼리에 @trace_tool 적용 (절대 규칙 #3).
"""
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.trace import trace_tool

# 신장 위구르 자치구 경계 (스펙 5-2). SRID 4326 기준.
XINJIANG_REGION_WKT = (
    "POLYGON((73.4 34.8, 96.4 34.8, 96.4 49.2, 73.4 49.2, 73.4 34.8))"
)


class SupplyChainRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @trace_tool("supply_chain_tree_query")
    async def get_n_tier_supply_chain(
        self,
        product_id: str,
    ) -> List[Dict[str, Any]]:
        """
        특정 product_id에 연결된 Tier 1~말단 광산까지 전체 트리를 재귀 탐색.
        스펙 5-1 SUPPLY_CHAIN_TREE_QUERY 기준: bom_versions JOIN으로 product_id 진입,
        원청(parent_supplier_id IS NULL)부터 하향 탐색.
        순환 참조(Cycle) 방지 path 추적 포함.
        공장 좌표는 GeoJSON으로 반환 (스펙 완료 기준).
        """
        query = text("""
            WITH RECURSIVE sc_tree AS (
                SELECT
                    scm.map_id, scm.parent_supplier_id, scm.child_supplier_id,
                    scm.part_id, s.company_name, s.supplier_type, scm.hop_level,
                    sf.country,
                    ST_AsGeoJSON(sf.location) AS location_geojson,
                    1 AS depth,
                    ARRAY[scm.child_supplier_id] AS path,
                    FALSE AS is_cycle
                FROM supply_chain_map scm
                JOIN bom_versions bv ON bv.bom_version_id = scm.bom_version_id
                JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
                LEFT JOIN supplier_factories sf
                    ON sf.supplier_id = s.supplier_id AND sf.is_active = TRUE
                WHERE bv.product_id = :product_id
                  AND scm.parent_supplier_id IS NULL

                UNION ALL

                SELECT
                    scm.map_id, scm.parent_supplier_id, scm.child_supplier_id,
                    scm.part_id, s.company_name, s.supplier_type, scm.hop_level,
                    sf.country,
                    ST_AsGeoJSON(sf.location) AS location_geojson,
                    sct.depth + 1,
                    sct.path || scm.child_supplier_id,
                    scm.child_supplier_id = ANY(sct.path)
                FROM supply_chain_map scm
                JOIN sc_tree sct ON scm.parent_supplier_id = sct.child_supplier_id
                JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
                LEFT JOIN supplier_factories sf
                    ON sf.supplier_id = s.supplier_id AND sf.is_active = TRUE
                WHERE NOT sct.is_cycle
            )
            SELECT
                map_id, parent_supplier_id, child_supplier_id, part_id,
                company_name, supplier_type, hop_level, country,
                location_geojson, depth, is_cycle
            FROM sc_tree
            ORDER BY depth, hop_level;
        """)
        result = await self.session.execute(query, {"product_id": product_id})
        return [dict(row._mapping) for row in result]

    @trace_tool("supply_chain_create")
    async def create_supply_relation(
        self,
        bom_version_id: str,
        parent_supplier_id: str | None,
        child_supplier_id: str,
        part_id: str,
    ) -> Dict[str, Any]:
        """supply_chain_map에 parent-child 관계 INSERT 후 생성 row 반환."""
        query = text("""
            INSERT INTO supply_chain_map
                (bom_version_id, parent_supplier_id, child_supplier_id, part_id)
            VALUES
                (:bom_version_id, :parent_supplier_id, :child_supplier_id, :part_id)
            RETURNING map_id, parent_supplier_id, child_supplier_id, part_id;
        """)
        result = await self.session.execute(query, {
            "bom_version_id": bom_version_id,
            "parent_supplier_id": parent_supplier_id,
            "child_supplier_id": child_supplier_id,
            "part_id": part_id,
        })
        await self.session.commit()
        return dict(result.first()._mapping)

    @trace_tool("cycle_precheck")
    async def would_create_cycle(
        self,
        parent_supplier_id: str,
        child_supplier_id: str,
    ) -> bool:
        """
        child가 parent의 상위(조상)이면 순환이 생긴다.
        child를 루트로 하향 탐색했을 때 parent에 도달하면 True.
        """
        query = text("""
            WITH RECURSIVE descendants AS (
                SELECT child_supplier_id
                FROM supply_chain_map
                WHERE parent_supplier_id = :child_id

                UNION ALL

                SELECT scm.child_supplier_id
                FROM supply_chain_map scm
                JOIN descendants d ON scm.parent_supplier_id = d.child_supplier_id
            )
            SELECT EXISTS (
                SELECT 1 FROM descendants WHERE child_supplier_id = :parent_id
            ) AS has_cycle;
        """)
        result = await self.session.execute(query, {
            "child_id": child_supplier_id,
            "parent_id": parent_supplier_id,
        })
        return bool(result.scalar())

    @trace_tool("supply_ratio_sum")
    async def get_ratio_sum_for_map(self, map_id: str) -> float:
        """해당 map_id에 등록된 supply_ratio.ratio_percentage 합. 100 초과 검증용."""
        query = text("""
            SELECT COALESCE(SUM(ratio_percentage), 0) AS total
            FROM supply_ratio
            WHERE map_id = :map_id;
        """)
        result = await self.session.execute(query, {"map_id": map_id})
        return float(result.scalar() or 0)

    @trace_tool("alternatives_query")
    async def get_alternatives(
        self,
        product_id: str,
        part_id: str,
    ) -> List[Dict[str, Any]]:
        """동일 part_id를 공급하는 다른 협력사 목록 (대체 공급망)."""
        query = text("""
            SELECT DISTINCT
                s.supplier_id, s.company_name, s.supplier_type, scm.hop_level,
                sr.ratio_percentage
            FROM supply_chain_map scm
            JOIN bom_versions bv ON bv.bom_version_id = scm.bom_version_id
            JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
            LEFT JOIN supply_ratio sr ON sr.map_id = scm.map_id
            WHERE bv.product_id = :product_id
              AND scm.part_id = :part_id
            ORDER BY sr.ratio_percentage DESC NULLS LAST;
        """)
        result = await self.session.execute(query, {
            "product_id": product_id,
            "part_id": part_id,
        })
        return [dict(row._mapping) for row in result]

    @trace_tool("xinjiang_proximity_check")
    async def check_geo_audit_risk_zone(
        self,
        radius_meters: int = 50000,  # 스펙 5-2: 50km 이내
    ) -> List[Dict[str, Any]]:
        """
        협력사 공장/광산 좌표가 신장 위구르 자치구 경계(Polygon) 반경 내(기본 50km)인지 검증.
        ST_DWithin (geography 캐스팅으로 미터 단위) 사용.
        """
        query = text("""
            SELECT
                s.supplier_id, s.company_name,
                sf.factory_id, sf.factory_name, sf.country,
                ST_AsGeoJSON(sf.location) AS coordinates,
                ST_DWithin(
                    sf.location::geography,
                    ST_GeomFromText(:xinjiang_wkt, 4326)::geography,
                    :radius
                ) AS is_in_risk_zone,
                ST_Distance(
                    sf.location::geography,
                    ST_GeomFromText(:xinjiang_wkt, 4326)::geography
                ) / 1000.0 AS distance_km
            FROM supplier_factories sf
            JOIN suppliers s ON sf.supplier_id = s.supplier_id
            WHERE sf.location IS NOT NULL;
        """)
        result = await self.session.execute(query, {
            "xinjiang_wkt": XINJIANG_REGION_WKT, 
            "radius": radius_meters,
        })
        return [dict(row._mapping) for row in result]

    @trace_tool("coordinate_authenticity")
    async def check_coordinate_authenticity(self, db: AsyncSession) -> List[Dict]:
        """
        공장 좌표(location)가 신고된 국가(country)의 폴리곤 경계 안에 위치하는지 대조.
        실제 운영 환경에서는 국가별 다각형(MultiPolygon) 테이블과 JOIN하지만,
        현재 시연을 위해 CTE로 주요 국가의 바운딩 박스(ST_MakeEnvelope)를 임시 구성하여 검증합니다.
        """
        query = text("""
            WITH mock_country_boundaries AS (
                -- 시연용 주요 국가 바운딩 박스 (BBOX - MinX, MinY, MaxX, MaxY)
                -- 중국(CN), 베트남(VN), 한국(KR), 미국(US)
                SELECT 'CN' AS country_code, ST_MakeEnvelope(73.0, 18.0, 135.0, 53.0, 4326) AS geom UNION ALL
                SELECT 'VN' AS country_code, ST_MakeEnvelope(102.0, 8.0, 109.0, 23.0, 4326) AS geom UNION ALL
                SELECT 'KR' AS country_code, ST_MakeEnvelope(124.0, 33.0, 132.0, 39.0, 4326) AS geom UNION ALL
                SELECT 'US' AS country_code, ST_MakeEnvelope(-125.0, 24.0, -66.0, 49.0, 4326) AS geom
            )
            SELECT
                sf.factory_id,
                s.supplier_id,
                s.company_name,
                ST_AsGeoJSON(sf.location) AS coordinates,
                sf.country,
                CASE
                    -- 경계 데이터가 정의된 국가라면 내부에 있는지 ST_Within으로 판정
                    WHEN mb.geom IS NOT NULL THEN ST_Within(sf.location, mb.geom)
                    -- 경계 데이터가 없는 국가는 시나리오 진행을 위해 임시로 True 처리
                    ELSE TRUE
                END AS country_match
            FROM supplier_factories sf
            JOIN suppliers s ON sf.supplier_id = s.supplier_id
            LEFT JOIN mock_country_boundaries mb ON sf.country = mb.country_code
            WHERE sf.is_active = TRUE 
              AND sf.location IS NOT NULL;
        """)
        # session은 의존성 주입된 self.session 사용. 인자 db는 상위 서비스 호출 호환을 위해 유지합니다.
        result = await self.session.execute(query)
        return [dict(row._mapping) for row in result]
