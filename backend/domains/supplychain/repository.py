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
        특정 product_id에 연결된 원청(Pack)~말단 광산까지 전체 트리를 재귀 탐색.
        스펙 5-1 SUPPLY_CHAIN_TREE_QUERY 기준: bom_versions JOIN으로 product_id 진입,
        트리 루트 = 원청(tier0/hop0, parent_supplier_id IS NULL → child=원청 Pack)부터 하향 탐색.

        [F1 표시 기준 — depth 단일화]
          - depth = CTE 재귀 깊이(0=원청). 프론트 트리 렌더링·레이어 표시 기준축.
          - hop_level = supply_chain_map 엣지 보조 메타(경로 순번). 재귀 JOIN 조건·겸업 탐색용.
            겸업(한양셀 Module→Cell)처럼 depth ≠ hop_level 이 될 수 있다 → 표시는 depth만 사용.
          - 순환 판정: path 키 = (child_supplier_id, part_id) 복합키(겸업 오판 방지).
        공장 좌표는 GeoJSON으로 반환 (스펙 완료 기준).
        """
        query = text("""
            WITH RECURSIVE sc_tree AS (
                -- 앵커: 트리 루트 = 원청 (parent_supplier_id IS NULL, child=원청 Pack, hop_level=0)
                SELECT
                    scm.map_id, scm.bom_version_id, scm.parent_supplier_id, scm.child_supplier_id,
                    scm.part_id, s.company_name, s.supplier_type, scm.hop_level,
                    sf.country,
                    ST_AsGeoJSON(sf.location) AS location_geojson,
                    0 AS depth,
                    ARRAY[scm.child_supplier_id::text || ':' || scm.part_id::text] AS path,
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
                    scm.map_id, scm.bom_version_id, scm.parent_supplier_id, scm.child_supplier_id,
                    scm.part_id, s.company_name, s.supplier_type, scm.hop_level,
                    sf.country,
                    ST_AsGeoJSON(sf.location) AS location_geojson,
                    sct.depth + 1,
                    sct.path || (scm.child_supplier_id::text || ':' || scm.part_id::text),
                    (scm.child_supplier_id::text || ':' || scm.part_id::text) = ANY(sct.path)
                FROM supply_chain_map scm
                JOIN sc_tree sct ON scm.parent_supplier_id = sct.child_supplier_id
                                AND scm.bom_version_id = sct.bom_version_id
                                AND scm.hop_level = sct.hop_level + 1
                JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
                LEFT JOIN supplier_factories sf
                    ON sf.supplier_id = s.supplier_id AND sf.is_active = TRUE
                WHERE NOT sct.is_cycle
            )
            SELECT
                map_id, parent_supplier_id, child_supplier_id, part_id,
                company_name, supplier_type,
                depth,       -- [F1 주축] 프론트 트리 표시 기준
                hop_level,   -- [F1 보조] 엣지 메타 — 겸업 탐색·JOIN 조건용
                country, location_geojson, is_cycle
            FROM sc_tree
            ORDER BY depth, hop_level;
        """)
        result = await self.session.execute(query, {"product_id": product_id})
        return [dict(row._mapping) for row in result]

    @trace_tool("supply_chain_by_bom_depth")
    async def get_by_bom_depth(self, bom_depth: int) -> List[Dict[str, Any]]:
        """
        부품 tier 기준 필터 (bom_depth = parts.tier_level, 0-base).
        '같은 부품 계층(Pack/Module/Cell/활물질/전구체/제련/광산)' 노드만 횡으로 조회.
        hop_level(경로 순번)과는 독립축이므로 ADR에 따라 엔드포인트를 분리한다.
        """
        query = text("""
            SELECT
                scm.map_id, scm.bom_version_id, scm.parent_supplier_id, scm.child_supplier_id,
                scm.part_id, s.company_name, s.supplier_type,
                scm.hop_level, p.tier_level AS bom_depth, scm.link_status
            FROM supply_chain_map scm
            JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
            JOIN parts p ON p.part_id = scm.part_id
            WHERE p.tier_level = :bom_depth
            ORDER BY scm.hop_level, s.company_name;
        """)
        result = await self.session.execute(query, {"bom_depth": bom_depth})
        return [dict(row._mapping) for row in result]

    @trace_tool("supply_chain_by_hop")
    async def get_by_hop(self, hop_level: int) -> List[Dict[str, Any]]:
        """
        공급망 차수 기준 필터 (hop_level = 원청 0 기준 경로 순번).
        '같은 공급망 차수' 노드만 횡으로 조회. bom_depth(부품 tier)와는 독립축.
        """
        query = text("""
            SELECT
                scm.map_id, scm.bom_version_id, scm.parent_supplier_id, scm.child_supplier_id,
                scm.part_id, s.company_name, s.supplier_type,
                scm.hop_level, p.tier_level AS bom_depth, scm.link_status
            FROM supply_chain_map scm
            JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
            LEFT JOIN parts p ON p.part_id = scm.part_id
            WHERE scm.hop_level = :hop_level
            ORDER BY s.company_name;
        """)
        result = await self.session.execute(query, {"hop_level": hop_level})
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

    @trace_tool("supply_chain_declare_source")
    async def declare_new_source(
        self,
        bom_version_id: str,
        parent_supplier_id: str,
        child_supplier_id: str,
        part_id: str,
    ) -> Dict[str, Any]:
        """협력사 자진신고: 공급원 변경 시 새로운 노드를 SUPPLIER_DECLARED 상태로 생성"""
        query = text("""
            INSERT INTO supply_chain_map
                (bom_version_id, parent_supplier_id, child_supplier_id, part_id,
                 hop_level, link_status, source_system, verification_status)
            VALUES
                (:bom_version_id, :parent_supplier_id, :child_supplier_id, :part_id,
                 COALESCE((SELECT hop_level + 1 FROM supply_chain_map
                           WHERE child_supplier_id = :parent_supplier_id
                             AND bom_version_id = :bom_version_id
                           LIMIT 1), 1),
                 'supplychain_declared', 'SUPPLIER_DECLARED', 'unverified')
            RETURNING map_id, parent_supplier_id, child_supplier_id, link_status, verification_status;
        """)
        result = await self.session.execute(query, {
            "bom_version_id": bom_version_id,
            "parent_supplier_id": parent_supplier_id,
            "child_supplier_id": child_supplier_id,
            "part_id": part_id,
        })
        await self.session.commit()
        return dict(result.first()._mapping)

    @trace_tool("get_supplier_master_and_gps_dto")
    async def get_supplier_master_and_gps_dto(self, supplier_id: str) -> dict:
        """HITL 컨텍스트용 협력사 마스터 및 공장 GPS 정보 조회"""
        master_query = text("""
            SELECT supplier_id, company_name, company_name_en, supplier_type,
                   status, risk_level, feoc_status, completeness_score,
                   (SELECT sf.country FROM supplier_factories sf
                    WHERE sf.supplier_id = suppliers.supplier_id AND sf.is_active = TRUE
                    ORDER BY (sf.factory_role = 'headquarters') DESC, sf.factory_id
                    LIMIT 1) AS country,
                   NULL::INT AS tier
            FROM suppliers
            WHERE supplier_id = :supplier_id
        """)
        master_res = await self.session.execute(master_query, {"supplier_id": supplier_id})
        master_row = master_res.mappings().first()

        if not master_row:
            return {"supplier_master": {}, "factory_gps": []}

        master_dict = dict(master_row)
        if master_dict.get("supplier_id"):
            master_dict["supplier_id"] = str(master_dict["supplier_id"])

        factory_query = text("""
            SELECT factory_id, factory_name, address, country, region, factory_role,
                   ST_Y(location) AS lat, ST_X(location) AS lng,
                   COALESCE(ST_Within(location, ST_GeomFromText(:xinjiang_wkt, 4326)), FALSE) AS in_xinjiang
            FROM supplier_factories
            WHERE supplier_id = :supplier_id AND is_active = TRUE
        """)
        factory_res = await self.session.execute(factory_query, {
            "supplier_id": supplier_id,
            "xinjiang_wkt": XINJIANG_REGION_WKT,
        })
        factory_rows = factory_res.mappings().all()

        gps_list = []
        for row in factory_rows:
            f_dict = dict(row)
            if f_dict.get("factory_id"):
                f_dict["factory_id"] = str(f_dict["factory_id"])
            gps_list.append(f_dict)

        return {"supplier_master": master_dict, "factory_gps": gps_list}

    @trace_tool("check_company_boundary")
    async def is_cross_company_boundary(self, supplier_a_id: str, supplier_b_id: str) -> bool:
        """회사 경계 확인: corporate_reg_no가 다르거나, 둘 다 없는데 ID가 다르면 다른 법인으로 취급"""
        query = text("""
            SELECT COALESCE(s1.corporate_reg_no, s1.supplier_id::text) != COALESCE(s2.corporate_reg_no, s2.supplier_id::text) AS is_cross
            FROM suppliers s1, suppliers s2
            WHERE s1.supplier_id = :sup_a AND s2.supplier_id = :sup_b;
        """)
        result = await self.session.execute(query, {"sup_a": supplier_a_id, "sup_b": supplier_b_id})
        return bool(result.scalar())

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

    # [BYPASS:A2] 시연용 4개국 바운딩박스 — 미정의 국가는 ELSE TRUE 통과. 운영 전환 시 국가 폴리곤 테이블 필요
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

    # [BYPASS:A3] 시연용 가상 산림훼손지(보르네오 박스 1개) — 운영 전환 시 GFW 등 실데이터 필요
    @trace_tool("check_eudr_deforestation")
    async def check_eudr_deforestation(self, db: AsyncSession) -> List[Dict[str, Any]]:
        """
        EUDR(산림 훼손) 위험지역 검사를 수행합니다.
        시연을 위해 특정 좌표계(ST_MakeEnvelope)를 가상의 위험 폴리곤으로 간주하고,
        공장 좌표가 그 내부에(ST_Within) 있는지 검사합니다.
        """
        query = text("""
            WITH eudr_risk_zone AS (
                -- 시연용 가상 산림 훼손지: 인도네시아 보르네오 섬 인근 임의 좌표 박스
                -- Longitude(X): 110.0 ~ 118.0 / Latitude(Y): -4.0 ~ 4.0
                SELECT ST_SetSRID(ST_MakeEnvelope(110.0, -4.0, 118.0, 4.0), 4326) AS geom
            )
            SELECT
                sf.factory_id,
                s.supplier_id,
                s.company_name,
                ST_AsGeoJSON(sf.location) AS coordinates,
                ST_Within(sf.location, r.geom) AS is_deforested
            FROM supplier_factories sf
            JOIN suppliers s ON sf.supplier_id = s.supplier_id
            CROSS JOIN eudr_risk_zone r
            WHERE sf.is_active = TRUE
              AND sf.location IS NOT NULL;
        """)
        
        result = await self.session.execute(query)
        return [dict(row._mapping) for row in result]
