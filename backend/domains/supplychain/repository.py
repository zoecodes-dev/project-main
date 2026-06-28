"""
domains/supplychain/repository.py  (담당: 팀원 D · 영수)

공급망 그래프 데이터 접근 계층. 재귀 CTE 기반 N차 탐색 + PostGIS Geo Audit.
모든 주요 쿼리에 @trace_tool 적용 (절대 규칙 #3).
"""
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.trace import trace_tool

# 신장 위구르 자치구 경계 (스펙 5-2). SRID 4326 기준.
XINJIANG_REGION_WKT = (
    "POLYGON((73.4 34.8, 96.4 34.8, 96.4 49.2, 73.4 49.2, 73.4 34.8))"
)


# [MARKER:BEGIN] supplier 외(supplychain) — 맵 헤더(supply_chain_maps) 도입에 따른 개명.
#   supply_chain_map.map_id(엣지 PK) → edge_id, supply_ratio.map_id → edge_id 로 전 쿼리 정합.
#   프론트 응답 키는 보호 위해 최종 출력에서 'map_id' 별칭/CTE 컬럼명 유지(edge_id AS map_id).
#   최종 작업 시 이 클래스의 map_id↔edge_id 관련 변경을 원복 대상으로 식별.
# [MARKER:END]
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
                    scm.edge_id AS map_id, scm.bom_version_id, scm.parent_supplier_id, scm.child_supplier_id,
                    scm.part_id, s.company_name, s.provider_type, scm.hop_level,
                    sf.country,
                    ST_AsGeoJSON(sf.location) AS location_geojson,
                    0 AS depth,
                    ARRAY[scm.child_supplier_id::text || ':' || scm.part_id::text] AS path,
                    FALSE AS is_cycle,
                    TRUE AS is_root_anchor
                FROM supply_chain_map scm
                JOIN bom_versions bv ON bv.bom_version_id = scm.bom_version_id
                JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
                LEFT JOIN supplier_factories sf
                    ON sf.supplier_id = s.supplier_id AND sf.is_active = TRUE
                WHERE bv.product_id = :product_id
                  AND scm.parent_supplier_id IS NULL

                UNION ALL

                SELECT
                    scm.edge_id AS map_id, scm.bom_version_id, scm.parent_supplier_id, scm.child_supplier_id,
                    scm.part_id, s.company_name, s.provider_type, scm.hop_level,
                    sf.country,
                    ST_AsGeoJSON(sf.location) AS location_geojson,
                    sct.depth + 1,
                    sct.path || (scm.child_supplier_id::text || ':' || scm.part_id::text),
                    (scm.child_supplier_id::text || ':' || scm.part_id::text) = ANY(sct.path),
                    FALSE AS is_root_anchor
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
                company_name, provider_type,
                depth,           -- [F1 주축] 프론트 트리 표시 기준
                hop_level,       -- [F1 보조] 엣지 메타 — 겸업 탐색·JOIN 조건용
                is_root_anchor,  -- [F2] parent_supplier_id IS NULL 파생 — OEM/tier0 동적 판정
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
                scm.edge_id AS map_id, scm.bom_version_id, scm.parent_supplier_id, scm.child_supplier_id,
                scm.part_id, s.company_name, s.provider_type,
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
                scm.edge_id AS map_id, scm.bom_version_id, scm.parent_supplier_id, scm.child_supplier_id,
                scm.part_id, s.company_name, s.provider_type,
                scm.hop_level, p.tier_level AS bom_depth, scm.link_status
            FROM supply_chain_map scm
            JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
            LEFT JOIN parts p ON p.part_id = scm.part_id
            WHERE scm.hop_level = :hop_level
            ORDER BY s.company_name;
        """)
        result = await self.session.execute(query, {"hop_level": hop_level})
        return [dict(row._mapping) for row in result]

    # [MARKER:BEGIN] supplier 외(supplychain) — 신규 엣지를 소속 맵 헤더에 연결.
    #     @trace_tool("supply_chain_create")
    #     async def _ensure_map_header(self, bom_version_id: str) -> str:
    #         """이 bom_version의 공급망 맵 헤더(supply_chain_maps) 보장 — 없으면 생성하고 map_id 반환."""
    #         q = text("""
    #             INSERT INTO supply_chain_maps (bom_version_id, product_id, status)
    #             SELECT :bv, bv.product_id, 'building' FROM bom_versions bv WHERE bv.bom_version_id = :bv
    #             ON CONFLICT (bom_version_id) DO UPDATE SET bom_version_id = EXCLUDED.bom_version_id
    #             RETURNING map_id;
    #         """)
    #         r = await self.session.execute(q, {"bv": bom_version_id})
    #         return str(r.scalar_one())
    # [MARKER:END]

    async def create_supply_relation(
        self,
        bom_version_id: str,
        parent_supplier_id: str | None,
        child_supplier_id: str,
        part_id: str,
    ) -> Dict[str, Any]:
        """supply_chain_map에 parent-child 관계 INSERT 후 생성 row 반환."""
        # map_header_id = await self._ensure_map_header(bom_version_id)  # [MARKER]
        query = text("""
            INSERT INTO supply_chain_map
                (map_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id)
            VALUES
                (:map_header_id, :bom_version_id, :parent_supplier_id, :child_supplier_id, :part_id)
            RETURNING edge_id AS map_id, parent_supplier_id, child_supplier_id, part_id;
        """)
        result = await self.session.execute(query, {
            "map_header_id": map_header_id,
            "bom_version_id": bom_version_id,
            "parent_supplier_id": parent_supplier_id,
            "child_supplier_id": child_supplier_id,
            "part_id": part_id,
        })
        await self.session.flush()
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
        # map_header_id = await self._ensure_map_header(bom_version_id)  # [MARKER] 헤더 연결
        query = text("""
            INSERT INTO supply_chain_map
                (map_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id,
                 hop_level, link_status, source_system, verification_status)
            VALUES
                (:map_header_id, :bom_version_id, :parent_supplier_id, :child_supplier_id, :part_id,
                 COALESCE((SELECT hop_level + 1 FROM supply_chain_map
                           WHERE child_supplier_id = :parent_supplier_id
                             AND bom_version_id = :bom_version_id
                           LIMIT 1), 1),
                 'supplychain_declared', 'SUPPLIER_DECLARED', 'unverified')
            RETURNING edge_id AS map_id, parent_supplier_id, child_supplier_id, link_status, verification_status;
        """)
        result = await self.session.execute(query, {
            "map_header_id": map_header_id,
            "bom_version_id": bom_version_id,
            "parent_supplier_id": parent_supplier_id,
            "child_supplier_id": child_supplier_id,
            "part_id": part_id,
        })
        await self.session.flush()
        return dict(result.first()._mapping)

    # [MARKER:BEGIN] 협력사 확인(verify) — supply_chain_map.verification_status 갱신.
    #   supplier 외(supplychain) 도메인. 최종 작업 시 이 메서드 전체 주석/삭제.
    #     async def set_supplier_verification(
    #         self,
    #         bom_version_id: str,
    #         child_supplier_id: str,
    #         verified: bool,
    #     ) -> int:
    #         """이 BOM 버전에서 해당 협력사로 연결된 맵 엣지들의 verification_status를 일괄 갱신."""
    #         query = text("""
    #             UPDATE supply_chain_map
    #                SET verification_status = :status
    #              WHERE bom_version_id = :bom_version_id
    #                AND child_supplier_id = :child_supplier_id
    #             RETURNING edge_id AS map_id;
    #         """)
    #         result = await self.session.execute(query, {
    #             "bom_version_id": bom_version_id,
    #             "child_supplier_id": child_supplier_id,
    #             "status": "verified" if verified else "unverified",
    #         })
    #         rows = result.fetchall()
    #         await self.session.flush()
    #         return len(rows)
    # [MARKER:END]

    @trace_tool("get_supplier_master_and_gps_dto")
    async def get_supplier_master_and_gps_dto(self, supplier_id: str) -> dict:
        """HITL 컨텍스트용 협력사 마스터 및 공장 GPS 정보 조회"""
        master_query = text("""
            SELECT supplier_id, company_name, company_name_en, provider_type,
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
            WHERE edge_id = :map_id;
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
                s.supplier_id, s.company_name, s.provider_type, scm.hop_level,
                sr.ratio_percentage
            FROM supply_chain_map scm
            JOIN bom_versions bv ON bv.bom_version_id = scm.bom_version_id
            JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
            LEFT JOIN supply_ratio sr ON sr.edge_id = scm.edge_id
            WHERE bv.product_id = :product_id
              AND scm.part_id = :part_id
            ORDER BY sr.ratio_percentage DESC NULLS LAST;
        """)
        result = await self.session.execute(query, {
            "product_id": product_id,
            "part_id": part_id,
        })
        return [dict(row._mapping) for row in result]

    @trace_tool("supply_chain_gaps_query")
    async def get_supplier_field_data(self, product_id: str) -> List[Dict[str, Any]]:
        """
        C2 gap 계산용: 제품 공급망 내 모든 고유 협력사와 각 규제 필수 필드의 보유 여부를 조회.

        반환 컬럼:
          supplier_id, provider_type, depth (트리 최소 depth)
          has_carbon_intensity          : manufacturer_details.carbon_intensity 존재 여부
          has_factory_carbon_decl       : factory_carbon_declarations 행 존재 여부
          has_recycled_content_ratio    : recycler_details.recycled_content_ratio 존재 여부
          has_recycled_materials        : recycler_details.recycled_materials 존재 여부
          has_mine_coordinates          : miner_details.mine_coordinates 존재 여부
          has_origin_country            : origin_certificates(valid/expiring_soon) 존재 여부
          has_feoc_direct_ownership     : risk_profiles.feoc_direct_ownership 존재 여부
          has_feoc_indirect_ownership   : risk_profiles.feoc_indirect_ownership 존재 여부
        """
        query = text("""
            WITH RECURSIVE sc_tree AS (
                SELECT
                    scm.child_supplier_id, s.provider_type,
                    0 AS depth
                FROM supply_chain_map scm
                JOIN bom_versions bv ON bv.bom_version_id = scm.bom_version_id
                JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
                WHERE bv.product_id = :product_id
                  AND scm.parent_supplier_id IS NULL

                UNION ALL

                SELECT
                    scm.child_supplier_id, s.provider_type,
                    sct.depth + 1
                FROM supply_chain_map scm
                JOIN sc_tree sct ON scm.parent_supplier_id = sct.child_supplier_id
                JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
            ),
            unique_suppliers AS (
                SELECT DISTINCT ON (child_supplier_id)
                    child_supplier_id AS supplier_id,
                    provider_type,
                    MIN(depth) OVER (PARTITION BY child_supplier_id) AS depth
                FROM sc_tree
                ORDER BY child_supplier_id, depth
            ),
            root_suppliers AS (
                SELECT DISTINCT child_supplier_id
                FROM sc_tree
                WHERE depth = 0
            )
            SELECT
                us.supplier_id,
                us.provider_type,
                us.depth,
                (rs.child_supplier_id IS NOT NULL) AS is_root_anchor,
                -- Manufacturer: carbon_intensity
                (smd.carbon_intensity IS NOT NULL)                           AS has_carbon_intensity,
                -- Manufacturer: factory_carbon_declarations (공장 단위 1차 선언)
                EXISTS (
                    SELECT 1 FROM factory_carbon_declarations fcd
                    JOIN supplier_factories sf ON sf.factory_id = fcd.factory_id
                    WHERE sf.supplier_id = us.supplier_id AND fcd.is_active = TRUE
                )                                                            AS has_factory_carbon_decl,
                -- Recycler: recycled_content_ratio
                (srd.recycled_content_ratio IS NOT NULL)                     AS has_recycled_content_ratio,
                -- Recycler: recycled_materials (JSONB — 광물별 함량)
                (srd.recycled_materials IS NOT NULL)                         AS has_recycled_materials,
                -- Miner: mine_coordinates (PostGIS POINT)
                (smind.mine_coordinates IS NOT NULL)                         AS has_mine_coordinates,
                -- Miner/Trader: origin_country via origin_certificates
                EXISTS (
                    SELECT 1 FROM origin_certificates oc
                    WHERE oc.supplier_id = us.supplier_id
                      AND oc.status IN ('valid', 'expiring_soon')
                )                                                            AS has_origin_country,
                -- Trader/Manufacturer: FEOC 직접 지분 (risk_profiles)
                (srp.feoc_direct_ownership IS NOT NULL)                      AS has_feoc_direct_ownership,
                -- Trader/Manufacturer: FEOC 간접 지분 (risk_profiles)
                (srp.feoc_indirect_ownership IS NOT NULL)                    AS has_feoc_indirect_ownership
            FROM unique_suppliers us
            LEFT JOIN root_suppliers rs                  ON rs.child_supplier_id = us.supplier_id
            LEFT JOIN supplier_manufacturer_details smd ON smd.supplier_id = us.supplier_id
            LEFT JOIN supplier_recycler_details srd      ON srd.supplier_id = us.supplier_id
            LEFT JOIN supplier_miner_details smind       ON smind.supplier_id = us.supplier_id
            LEFT JOIN supplier_risk_profiles srp         ON srp.supplier_id = us.supplier_id
            ORDER BY us.depth, us.provider_type;
        """)
        result = await self.session.execute(query, {"product_id": product_id})
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

    # -------------------------------------------------------------------------
    # 10.2a: 제품 공급망 맵 조회
    # -------------------------------------------------------------------------

    @trace_tool("supply_chain_map_by_product")
    async def get_supply_chain_map(
        self,
        product_id: str,
        tenant_id: str,
        bom_version_id: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
        factory_id: str | None = None,
        po_number: str | None = None,
    ) -> Dict[str, Any]:
        """
        제품 공급망 맵 조회 (10.2a).
        products.tenant_id → bom_versions → supply_chain_map 경로로 tenant 격리.
        반환: supply_chain_map / supply_chain_ratios / suppliers / supplier_factories
        """
        filters = [
            "bv.product_id = :product_id",
            "pr.tenant_id = :tenant_id",
        ]
        params: Dict[str, Any] = {"product_id": product_id, "tenant_id": tenant_id}

        if bom_version_id:
            filters.append("bv.bom_version_id = :bom_version_id")
            params["bom_version_id"] = bom_version_id
        if po_number:
            filters.append("scm.po_number = :po_number")
            params["po_number"] = po_number
        if period_from:
            filters.append("scm.supply_period_from >= :period_from")
            params["period_from"] = period_from
        if period_to:
            filters.append("scm.supply_period_to <= :period_to")
            params["period_to"] = period_to
        if factory_id:
            filters.append("EXISTS (SELECT 1 FROM supply_ratio sr2 WHERE sr2.edge_id = scm.edge_id AND sr2.factory_id = :factory_id)")
            params["factory_id"] = factory_id

        where = " AND ".join(filters)

        # 맵 노드 — 대표 factory_id는 첫 번째 supply_ratio에서 가져옴
        map_query = text(f"""
            SELECT DISTINCT
                scm.edge_id AS map_id,
                scm.part_id,
                scm.child_supplier_id  AS supplier_id,
                (
                    SELECT sr.factory_id FROM supply_ratio sr
                    WHERE sr.edge_id = scm.edge_id
                    ORDER BY sr.ratio_percentage DESC NULLS LAST
                    LIMIT 1
                )                      AS factory_id,
                p.tier_level,
                -- scm.hop_level,  [MARKER] supplier 외(supplychain) — 차수 SSOT(1차=hop 1). 프론트 1차 판정/트리 tier용
                -- p.part_name,   [MARKER] supplier 외(supplychain) — 프론트 맵 트리 부품명 표시용
                -- p.part_code,   [MARKER]
                scm.link_status,
                -- scm.verification_status,  [MARKER] STEP3 협력사 '확인' 상태 하이드레이션용
                scm.supply_period_from,
                scm.supply_period_to,
                scm.created_at
            FROM supply_chain_map scm
            JOIN bom_versions bv ON bv.bom_version_id = scm.bom_version_id
            JOIN products pr     ON pr.product_id = bv.product_id
            LEFT JOIN parts p    ON p.part_id = scm.part_id
            WHERE {where}
            ORDER BY p.tier_level NULLS LAST, scm.edge_id
        """)
        map_rows = await self.session.execute(map_query, params)
        supply_chain_map = [dict(r._mapping) for r in map_rows]

        # 누적 기여도 트리 (루트→공장 경로 ratio 곱). 구조 필터(product/tenant/bom_version)만 적용 —
        # period/po/factory 같은 행 단위 필터는 누적곱 경로를 끊으므로 여기선 제외(맵 배열엔 적용됨).
        contributions = await self.get_supply_chain_contributions(
            product_id=product_id,
            tenant_id=tenant_id,
            bom_version_id=bom_version_id,
        )

        # 비율 테이블 — 기존 계약({part_id, supplier_id, ratio_percent}) 유지 + 누적곱/매핑키 덧붙임.
        # ratio_percent 가 실제로 있는(supply_ratio 존재) 엣지만 포함(기존 INNER JOIN 의미 보존).
        supply_chain_ratios = [
            {
                "part_id":                 c["part_id"],
                "supplier_id":             c["supplier_id"],
                "ratio_percent":           c["ratio_percent"],
                "map_id":                  c["map_id"],
                "factory_id":              c["factory_id"],
                "cumulative_contribution": c["cumulative_contribution"],
            }
            for c in contributions
            if c["ratio_percent"] is not None
        ]

        # 계층별 합 100% 검증 (엣지별 공장합 / 공급사별 묶음합)
        validation = await self.get_supply_chain_validation(
            product_id=product_id,
            tenant_id=tenant_id,
            bom_version_id=bom_version_id,
        )

        # 공급사 브리프 — 맵에 등장하는 고유 supplier_id
        supplier_ids = list({str(r["supplier_id"]) for r in supply_chain_map if r["supplier_id"]})
        suppliers: List[Dict[str, Any]] = []
        if supplier_ids:
            # 내부용 tenant_id는 응답에서 제외 — 프론트 불필요 필드.
            # (suppliers는 이미 tenant 격리된 supply_chain_map에 등장하는 노드로 한정됨)
            sup_query = text("""
                SELECT
                    s.supplier_id, s.company_name, s.provider_type, s.status, s.risk_level,
                    s.feoc_status, s.completeness_score
                FROM suppliers s
                WHERE s.supplier_id = ANY(:ids)
            """)
            sup_rows = await self.session.execute(sup_query, {"ids": supplier_ids})
            suppliers = [dict(r._mapping) for r in sup_rows]

        # 공장 — supply_ratio에 등장하는 고유 factory_id
        factory_ids = list({str(r["factory_id"]) for r in supply_chain_map if r["factory_id"]})
        supplier_factories: List[Dict[str, Any]] = []
        if factory_ids:
            fac_query = text("""
                SELECT
                    sf.factory_id, sf.supplier_id, sf.factory_name, sf.address,
                    sf.country, sf.region, sf.factory_role,
                    ST_Y(sf.location) AS latitude,
                    ST_X(sf.location) AS longitude,
                    sf.is_active
                FROM supplier_factories sf
                WHERE sf.factory_id = ANY(:ids)
            """)
            fac_rows = await self.session.execute(fac_query, {"ids": factory_ids})
            supplier_factories = [dict(r._mapping) for r in fac_rows]

        return {
            "supply_chain_map": supply_chain_map,
            "supply_chain_ratios": supply_chain_ratios,
            "supply_chain_contributions": contributions,
            "validation": validation,
            "suppliers": suppliers,
            "supplier_factories": supplier_factories,
        }

    @trace_tool("supply_chain_map_confirm")
    async def confirm_map(
        self,
        map_id: str,
        tenant_id: str,
    ) -> Dict[str, Any] | None:
        """
        10.2b: supply_chain_map.link_status → supplychain_confirmed.
        products.tenant_id 경로로 tenant 격리 — 타 테넌트면 None 반환(→404).
        """
        query = text("""
            UPDATE supply_chain_map scm
            SET link_status = 'supplychain_confirmed'
            FROM bom_versions bv
            JOIN products pr ON pr.product_id = bv.product_id
            WHERE scm.bom_version_id = bv.bom_version_id
              AND scm.edge_id = :map_id
              AND pr.tenant_id = :tenant_id
            RETURNING scm.edge_id AS map_id, scm.link_status AS status
        """)
        result = await self.session.execute(query, {"map_id": map_id, "tenant_id": tenant_id})
        await self.session.flush()
        row = result.first()
        if row is None:
            return None
        return {"map_id": str(row[0]), "status": row[1]}

    # -------------------------------------------------------------------------
    # 10.2a 누적 기여도(곱셈 전파) + 계층별 합 100% 검증
    #   ratio_percentage 는 "직속 부모 대비 상대값" (PM 확정) → 경로상 비율의 곱이 말단 기여도.
    #   예) c=20×30%=6%, d=20×70%=14%, e=80×50%=40%, f=80×50%=40% (합 100%)
    # -------------------------------------------------------------------------

    @trace_tool("supply_chain_contributions")
    async def get_supply_chain_contributions(
        self,
        product_id: str,
        tenant_id: str,
        bom_version_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        """
        재귀 CTE로 원청(parent NULL, hop0)부터 말단 공장까지 전개하며
        경로상 ratio_percentage(상대값)를 곱해 `cumulative_contribution`(말단 기여도 %)을 산출.

        - 엣지에 supply_ratio 행이 여러 개면(공장 분할) 공장 단위로 행이 갈라진다.
        - supply_ratio 가 없는 엣지는 100% 패스스루(×1.0)로 취급해 누적곱 경로가 0/NULL로 끊기지 않게 한다.
        - 순환 판정: path 키 = (child_supplier_id, part_id) 복합키(겸업 self-edge 오판 방지, 기존 트리 CTE와 동일).
        """
        bom_filter = "AND bv.bom_version_id = :bom_version_id" if bom_version_id else ""
        params: Dict[str, Any] = {"product_id": product_id, "tenant_id": tenant_id}
        if bom_version_id:
            params["bom_version_id"] = bom_version_id

        query = text(f"""
            WITH RECURSIVE sc_cum AS (
                -- 앵커: 원청 루트 엣지 (parent_supplier_id IS NULL, hop0)
                SELECT
                    scm.edge_id AS map_id, scm.bom_version_id, scm.parent_supplier_id,
                    scm.child_supplier_id, scm.part_id, scm.hop_level, scm.link_status,
                    sr.factory_id,
                    sr.ratio_percentage,
                    COALESCE(sr.ratio_percentage / 100.0, 1.0) AS cum_ratio,
                    ARRAY[scm.child_supplier_id::text || ':' || scm.part_id::text] AS path,
                    FALSE AS is_cycle
                FROM supply_chain_map scm
                JOIN bom_versions bv ON bv.bom_version_id = scm.bom_version_id
                JOIN products pr     ON pr.product_id = bv.product_id
                LEFT JOIN supply_ratio sr ON sr.edge_id = scm.edge_id
                WHERE bv.product_id = :product_id
                  AND pr.tenant_id = :tenant_id
                  AND scm.parent_supplier_id IS NULL
                  {bom_filter}

                UNION ALL

                -- 재귀: 부모 child = 자식 parent, 같은 bom_version, hop_level +1 연속
                SELECT
                    scm.edge_id AS map_id, scm.bom_version_id, scm.parent_supplier_id,
                    scm.child_supplier_id, scm.part_id, scm.hop_level, scm.link_status,
                    sr.factory_id,
                    sr.ratio_percentage,
                    c.cum_ratio * COALESCE(sr.ratio_percentage / 100.0, 1.0) AS cum_ratio,
                    c.path || (scm.child_supplier_id::text || ':' || scm.part_id::text),
                    (scm.child_supplier_id::text || ':' || scm.part_id::text) = ANY(c.path)
                FROM supply_chain_map scm
                JOIN sc_cum c ON scm.parent_supplier_id = c.child_supplier_id
                             AND scm.bom_version_id = c.bom_version_id
                             AND scm.hop_level = c.hop_level + 1
                LEFT JOIN supply_ratio sr ON sr.edge_id = scm.edge_id
                WHERE NOT c.is_cycle
            )
            SELECT
                map_id,
                part_id,
                child_supplier_id      AS supplier_id,
                parent_supplier_id,
                factory_id,
                hop_level,
                link_status,
                ratio_percentage       AS ratio_percent,
                ROUND((cum_ratio * 100.0)::numeric, 4) AS cumulative_contribution
            FROM sc_cum
            WHERE NOT is_cycle
            ORDER BY hop_level, map_id;
        """)
        result = await self.session.execute(query, params)
        return [dict(row._mapping) for row in result]

    @trace_tool("supply_chain_validation")
    async def get_supply_chain_validation(
        self,
        product_id: str,
        tenant_id: str,
        bom_version_id: str | None = None,
    ) -> Dict[str, Any]:
        """
        계층별 비율 합 100% 검증 (차단용 아님 — 프론트 경고 표시용).
          - edges : 엣지(map_id)별 공장 분할 ratio 합 (공장 합 100% 대상)
          - tiers : 같은 (parent_supplier_id, part_id) 묶음의 자식 엣지 비율 합 (공급사 분할 100% 대상)
        합이 100 ±0.01 을 벗어나면 ok=false.
        """
        bom_filter = "AND bv.bom_version_id = :bom_version_id" if bom_version_id else ""
        params: Dict[str, Any] = {"product_id": product_id, "tenant_id": tenant_id}
        if bom_version_id:
            params["bom_version_id"] = bom_version_id

        edge_query = text(f"""
            SELECT
                scm.edge_id AS map_id,
                SUM(sr.ratio_percentage) AS total
            FROM supply_chain_map scm
            JOIN bom_versions bv ON bv.bom_version_id = scm.bom_version_id
            JOIN products pr     ON pr.product_id = bv.product_id
            JOIN supply_ratio sr ON sr.edge_id = scm.edge_id
            WHERE bv.product_id = :product_id
              AND pr.tenant_id = :tenant_id
              {bom_filter}
            GROUP BY scm.edge_id
        """)
        edge_rows = await self.session.execute(edge_query, params)
        edges = [
            {
                "map_id": str(r._mapping["map_id"]),
                "sum": float(r._mapping["total"] or 0),
                "ok": abs(float(r._mapping["total"] or 0) - 100.0) <= 0.01,
            }
            for r in edge_rows
        ]

        tier_query = text(f"""
            SELECT
                scm.parent_supplier_id,
                scm.part_id,
                SUM(sr.ratio_percentage) AS total
            FROM supply_chain_map scm
            JOIN bom_versions bv ON bv.bom_version_id = scm.bom_version_id
            JOIN products pr     ON pr.product_id = bv.product_id
            JOIN supply_ratio sr ON sr.edge_id = scm.edge_id
            WHERE bv.product_id = :product_id
              AND pr.tenant_id = :tenant_id
              AND scm.parent_supplier_id IS NOT NULL
              {bom_filter}
            GROUP BY scm.parent_supplier_id, scm.part_id
        """)
        tier_rows = await self.session.execute(tier_query, params)
        tiers = [
            {
                "parent_supplier_id": str(r._mapping["parent_supplier_id"]),
                "part_id": str(r._mapping["part_id"]),
                "sum": float(r._mapping["total"] or 0),
                "ok": abs(float(r._mapping["total"] or 0) - 100.0) <= 0.01,
            }
            for r in tier_rows
        ]

        all_valid = all(e["ok"] for e in edges) and all(t["ok"] for t in tiers)
        return {"edges": edges, "tiers": tiers, "all_valid": all_valid}

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

    # [MARKER:BEGIN] supplier 외(supplychain) — 공급망 맵 헤더(supply_chain_maps) 조회/상태.
    #     async def list_map_headers(self, tenant_id: str) -> List[Dict[str, Any]]:
    #         """내 테넌트의 공급망 맵 헤더 목록 + 엣지 수. (맵 그 자체를 map_id로 관리)"""
    #         query = text("""
    #             SELECT h.map_id, h.bom_version_id, h.product_id, p.product_name,
    #                    h.status, h.completed_at, COUNT(e.edge_id) AS edge_count
    #             FROM supply_chain_maps h
    #             JOIN products p ON p.product_id = h.product_id
    #             LEFT JOIN supply_chain_map e ON e.map_id = h.map_id
    #             WHERE p.tenant_id = :tenant_id
    #             GROUP BY h.map_id, h.bom_version_id, h.product_id, p.product_name, h.status, h.completed_at
    #             ORDER BY p.product_name;
    #         """)
    #         result = await self.session.execute(query, {"tenant_id": tenant_id})
    #         return [dict(r._mapping) for r in result]
    #
    #     async def get_map_header(self, map_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    #         """맵 헤더 단건(map_id). 내 테넌트 소유만(아니면 None)."""
    #         query = text("""
    #             SELECT h.map_id, h.bom_version_id, h.product_id, p.product_name,
    #                    h.status, h.completed_by, h.completed_at,
    #                    COUNT(e.edge_id) AS edge_count
    #             FROM supply_chain_maps h
    #             JOIN products p ON p.product_id = h.product_id
    #             LEFT JOIN supply_chain_map e ON e.map_id = h.map_id
    #             WHERE h.map_id = :map_id AND p.tenant_id = :tenant_id
    #             GROUP BY h.map_id, h.bom_version_id, h.product_id, p.product_name,
    #                      h.status, h.completed_by, h.completed_at;
    #         """)
    #         row = (await self.session.execute(query, {"map_id": map_id, "tenant_id": tenant_id})).first()
    #         return dict(row._mapping) if row else None
    #
    #     async def set_map_status(self, map_id: str, status: str, user_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    #         """맵 완료/전송 상태 변경. 내 테넌트 소유만. flush만(커밋은 service)."""
    #         is_completed = status == "completed"
    #         query = text("""
    #             UPDATE supply_chain_maps h
    #                SET status = :status,
    #                    completed_by = CASE WHEN :is_completed THEN CAST(:user_id AS uuid) ELSE h.completed_by END,
    #                    completed_at = CASE WHEN :is_completed THEN now() ELSE h.completed_at END
    #             FROM products p
    #              WHERE h.product_id = p.product_id
    #                AND h.map_id = :map_id AND p.tenant_id = :tenant_id
    #             RETURNING h.map_id, h.status, h.completed_at;
    #         """)
    #         row = (await self.session.execute(query, {
    #             "map_id": map_id, "status": status, "is_completed": is_completed,
    #             "user_id": user_id, "tenant_id": tenant_id,
    #         })).first()
    #         await self.session.flush()
    #         return dict(row._mapping) if row else None
    # [MARKER:END]
