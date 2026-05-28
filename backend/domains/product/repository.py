# =============================================================================
# backend/domains/product/repository.py
#
# KIRA Compliance Intelligence Platform — Product Domain DB Query Layer
#
# 역할: Product 도메인의 모든 DB 입출력 담당.
#   - fetch_from_source : 외부 원천(ERP/MES/시드) ingest + UPSERT  ← 결정 #1 신설
#   - _upsert_product   : fetch_from_source 내부 헬퍼 (직접 호출 금지)
#   - get_product       : 제품 단건 조회
#   - list_products     : 제품 목록 조회
#   - get_bom_tree      : 5계층 BOM 트리 재귀 CTE 조회 (결정 #2 only_confirmed 추가)
#
# [결정 #1 반영]
#   이 시스템은 제품을 직접 "생성"하지 않는다.
#   원청 ERP/MES/PLM 이 원천. fetch_from_source()가 그 통로.
#   시연에서는 원천 = 시드 데이터(source_system='SEED').
#   실환경에서는 이 함수 내부 데이터 소스만 ERP API로 교체하면 됨.
#
# [결정 #2 반영]
#   get_bom_tree()에 only_confirmed 스위치 추가.
#   supply_chain_map.link_status = 'confirmed' 필터 (schema 업데이트 선행 필요).
#
# 계층 규칙 (PROJECT_CORE.md 5-1):
#   - router.py → service.py → repository.py 단방향 호출.
#   - 타 도메인 import 없음.
#   - 이벤트 발행 없음 (이벤트는 service.py 책임).
# =============================================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.product.models import (
    BomVersion,
    Product,
    VALID_SOURCE_SYSTEMS,
)
from backend.infrastructure.trace import trace_tool


class ProductRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # -----------------------------------------------------------------------
    # fetch_from_source
    # -----------------------------------------------------------------------

    @trace_tool("fetch_from_source")
    async def fetch_from_source(
        self,
        source_system: str = "SEED",
    ) -> List[Product]:
        """
        [결정 #1] 외부 원천에서 제품 데이터를 읽어 products 테이블에 UPSERT한다.

        시연 구현:
            원천 = 이 DB에 이미 적재된 시드 데이터.
            source_system='SEED' 로 태깅해 ingest 경로를 추적 가능하게 함.

        실환경 전환:
            이 함수 내부의 "원천 조회" 부분만 ERP REST API 호출로 교체.
            함수 시그니처·UPSERT 로직·이벤트 발행 연계는 변경 불필요.

        발표 포인트:
            "ingest 레이어가 있고, 지금은 시드를 읽지만
             실제 환경에서는 ERP가 들어온다."

        [세팅 규칙]
            source_system : 파라미터로 전달받은 값 그대로 ('SEED' / 'ERP' / 'MES' / 'PLM')
            external_id   : 원천 시스템의 PK 문자열. 시연에서는 product_code를 그대로 사용.
            synced_at     : datetime.now(timezone.utc) — datetime.utcnow() 사용 금지(deprecated).

        [UPSERT 전략]
            ON CONFLICT (product_code) DO UPDATE
            → 같은 product_code가 이미 있으면 동기화 정보만 갱신.
            → 없으면 신규 INSERT.
            → idempotent — 동일 ingest를 두 번 실행해도 중복 row 없음.

        [반환]
            UPSERT된 Product ORM 객체 리스트.
        """
        if source_system not in VALID_SOURCE_SYSTEMS:
            raise ValueError(
                f"유효하지 않은 source_system: {source_system!r}. "
                f"허용값: {VALID_SOURCE_SYSTEMS}"
            )

        now = datetime.now(timezone.utc)

        # ------------------------------------------------------------------
        # 시연 원천 조회
        # 실환경 전환 시 이 블록을 ERP API 호출로 교체한다.
        # ------------------------------------------------------------------
        raw_products = await self._load_seed_products()

        # ------------------------------------------------------------------
        # UPSERT — product_code 충돌 시 동기화 정보 갱신
        # ------------------------------------------------------------------
        upserted: List[Product] = []

        for raw in raw_products:
            stmt = (
                pg_insert(Product)
                .values(
                    product_code=raw["product_code"],
                    product_name=raw.get("product_name"),
                    manufacturer_id=raw.get("manufacturer_id"),
                    type=raw.get("type"),
                    specs=raw.get("specs"),
                    source_system=source_system,
                    external_id=raw.get("external_id", raw["product_code"]),
                    synced_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["product_code"],
                    set_={
                        "product_name":  raw.get("product_name"),
                        "source_system": source_system,
                        "external_id":   raw.get("external_id", raw["product_code"]),
                        "synced_at":     now,
                        "updated_at":    now,
                    },
                )
                .returning(Product)
            )
            result = await self.session.execute(stmt)
            product = result.scalars().first()
            if product:
                upserted.append(product)

        await self.session.flush()
        return upserted

    async def _load_seed_products(self) -> List[Dict[str, Any]]:
        """
        시연용 시드 데이터를 DB에서 읽어 raw dict 리스트로 반환한다.

        [실환경 전환 포인트]
        이 함수를 ERP API 클라이언트 호출로 교체하면
        fetch_from_source() 나머지 로직은 그대로 재사용 가능.

        현재 구현: products 테이블에서 source_system IS NULL인 row
                   (= 시드에서 적재됐으나 아직 태깅 안 된 것)를 읽어 반환.
                   이미 'SEED'로 태깅된 row는 재처리 방지를 위해 제외하지 않음
                   (UPSERT의 ON CONFLICT가 멱등성 보장).
        """
        result = await self.session.execute(
            select(Product).order_by(Product.created_at.asc())
        )
        products = result.scalars().all()

        return [
            {
                "product_code":    p.product_code,
                "product_name":    p.product_name,
                "manufacturer_id": p.manufacturer_id,
                "type":            p.type,
                "specs":           p.specs,
                # external_id: 시연에서는 product_code를 원천 ID로 사용
                "external_id":     p.product_code,
            }
            for p in products
        ]

    # -----------------------------------------------------------------------
    # get_product
    # -----------------------------------------------------------------------

    @trace_tool("get_product")
    async def get_product(
        self,
        product_id: UUID,
    ) -> Optional[Product]:
        """
        product_id로 제품 단건을 조회한다.

        [반환]
        존재하면 Product ORM 객체, 없으면 None.
        """
        result = await self.session.execute(
            select(Product).where(Product.product_id == product_id)
        )
        return result.scalars().first()

    # -----------------------------------------------------------------------
    # list_products
    # -----------------------------------------------------------------------

    @trace_tool("list_products")
    async def list_products(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Product]:
        """
        제품 목록을 동기화일(synced_at) 내림차순으로 반환한다.

        [결정 #1] 정렬 기준을 created_at → synced_at 으로 변경.
                  동기화가 가장 최근인 제품이 상단에 오도록.
                  synced_at이 NULL인 row(미동기화)는 후순위.

        [페이지네이션]
        limit / offset 기반. 기본 20건.
        """
        result = await self.session.execute(
            select(Product)
            .order_by(
                Product.synced_at.desc().nulls_last(),
                Product.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # get_bom_tree
    # -----------------------------------------------------------------------

    @trace_tool("get_bom_tree")
    async def get_bom_tree(
        self,
        product_id: UUID,
        only_confirmed: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        product_id에 해당하는 제품의 active BOM 버전 기준
        5계층 BOM 트리를 반환한다.

        [파라미터]
        only_confirmed : bool = True
            [결정 #2] supply_chain_map.link_status 필터 스위치.
            True  → link_status = 'confirmed' 노드만 포함 (기본값, 운영 화면용).
            False → pending 포함 전체 트리 (공급망 맵 전체 뷰용).

            ⚠️ 현재 schema.sql의 supply_chain_map에 link_status 컬럼이 없음.
               결정 #2 일괄수정(schema migration) 완료 후 아래 주석 처리된
               link_status 필터를 해제한다.
               그 전까지는 only_confirmed 파라미터를 받되 필터는 미적용.

        [쿼리 전략]
        1단계 — products + bom_versions 조회
            product_id로 제품 정보와 active BOM 버전을 가져온다.
            active 버전이 없으면 None 반환 → service에서 404 처리.

        2단계 — WITH RECURSIVE CTE (depth < 5 상한)
            bom_version_id 기준으로 루트 부품(parent_part_id IS NULL)을
            앵커로 잡고, parent_part_id 관계를 따라 전 계층을 플랫하게 조회.
            depth < 5 조건으로 최대 5계층 초과 무한 루프 방지.

        3단계 — Python 트리 조립
            플랫한 rows를 part_id / parent_part_id 기반으로
            children 배열 중첩 구조로 조립한다.

        [반환]
        active BOM 버전이 존재하면 5계층 중첩 딕셔너리, 없으면 None.
        """

        # ------------------------------------------------------------------
        # 1단계: 제품 정보 + active BOM 버전 조회
        # ------------------------------------------------------------------
        product_query = text("""
            SELECT
                p.product_id,
                p.product_code,
                p.product_name,
                p.source_system,
                p.synced_at,
                bv.bom_version_id,
                bv.version_number,
                bv.status
            FROM products p
            JOIN bom_versions bv
                ON bv.product_id = p.product_id
            WHERE p.product_id = :product_id
              AND bv.status    = 'active'
            LIMIT 1
        """)

        product_result = await self.session.execute(
            product_query,
            {"product_id": str(product_id)},
        )
        product_row = product_result.mappings().first()

        if not product_row:
            # active BOM 버전 없음 — service에서 404로 처리
            return None

        bom_version_id = product_row["bom_version_id"]

        # ------------------------------------------------------------------
        # 2단계: WITH RECURSIVE CTE — 전 계층 플랫 조회
        #
        # [결정 #2] only_confirmed 필터:
        #   schema migration 완료 후 아래 supply_chain_map LEFT JOIN 블록의
        #   주석을 해제하고 link_status_filter 조건을 활성화한다.
        #
        #   활성화 방법:
        #     1. supply_chain_map LEFT JOIN 주석 해제
        #     2. WHERE 절에 link_status_filter 조건 추가:
        #        AND (scm.link_status = 'confirmed' OR scm.map_id IS NULL)
        #        -- scm.map_id IS NULL: supply_chain_map에 매핑 없는 부품도 포함
        #
        # [depth 상한]
        #   depth < 5 조건 필수 — parts 자기참조 순환 참조 방어.
        #   5계층(Pack=0 ~ 광물=4) 초과는 데이터 오염으로 간주.
        # ------------------------------------------------------------------

        # only_confirmed 파라미터 로깅 (schema migration 후 실제 필터로 교체)
        # TODO: schema migration 완료 시 아래 link_status_filter 주석 해제
        # link_status_filter = (
        #     "AND (scm.link_status = 'confirmed' OR scm.map_id IS NULL)"
        #     if only_confirmed
        #     else ""
        # )

        recursive_query = text("""
            WITH RECURSIVE bom_tree AS (

                -- 앵커: 루트 부품 (parent_part_id IS NULL, depth=0)
                SELECT
                    p.part_id,
                    p.part_code,
                    p.part_name,
                    p.tier_level,
                    p.parent_part_id,
                    p.hs_code,
                    p.material_type,
                    p.unit_price,
                    bi.required_quantity,
                    bi.required_quantity_unit,
                    bi.origin_country,
                    bi.direct_material_cost,
                    0 AS depth
                FROM parts p
                JOIN bom_items bi
                    ON bi.part_id        = p.part_id
                   AND bi.bom_version_id = :bom_version_id
                WHERE p.parent_part_id IS NULL

                UNION ALL

                -- 재귀: 직전 계층 부품의 자식 탐색 (depth < 5 상한)
                SELECT
                    p.part_id,
                    p.part_code,
                    p.part_name,
                    p.tier_level,
                    p.parent_part_id,
                    p.hs_code,
                    p.material_type,
                    p.unit_price,
                    bi.required_quantity,
                    bi.required_quantity_unit,
                    bi.origin_country,
                    bi.direct_material_cost,
                    bt.depth + 1
                FROM parts p
                JOIN bom_items bi
                    ON bi.part_id        = p.part_id
                   AND bi.bom_version_id = :bom_version_id
                JOIN bom_tree bt
                    ON p.parent_part_id  = bt.part_id
                WHERE bt.depth < 5

            )
            SELECT * FROM bom_tree
            ORDER BY depth, tier_level, part_code
        """)

        tree_result = await self.session.execute(
            recursive_query,
            {"bom_version_id": str(bom_version_id)},
        )
        rows = tree_result.mappings().all()

        if not rows:
            # BOM 버전은 있으나 bom_items가 비어 있는 경우
            # None 반환 → service에서 빈 트리 경고로 처리 (404 아님)
            return {
                "product_id":     str(product_row["product_id"]),
                "product_code":   product_row["product_code"],
                "product_name":   product_row["product_name"],
                "source_system":  product_row["source_system"],
                "synced_at":      str(product_row["synced_at"]) if product_row["synced_at"] else None,
                "bom_version":    product_row["version_number"],
                "bom_status":     product_row["status"],
                "only_confirmed": only_confirmed,
                "tree":           None,
                "warning":        "BOM 항목이 없습니다. 시드 데이터 또는 ingest 상태를 확인하세요.",
            }

        # ------------------------------------------------------------------
        # 3단계: 플랫 rows → children 중첩 트리 조립
        # ------------------------------------------------------------------
        node_map: Dict[str, Dict[str, Any]] = {}

        for row in rows:
            node = {
                "part_id":                str(row["part_id"]),
                "part_code":              row["part_code"],
                "part_name":              row["part_name"],
                "tier_level":             row["tier_level"],
                "parent_part_id":         str(row["parent_part_id"]) if row["parent_part_id"] else None,
                "hs_code":                row["hs_code"],
                "material_type":          row["material_type"],
                "unit_price":             float(row["unit_price"]) if row["unit_price"] is not None else None,
                "required_quantity":      float(row["required_quantity"]) if row["required_quantity"] is not None else None,
                "required_quantity_unit": row["required_quantity_unit"],
                "origin_country":         row["origin_country"],
                "direct_material_cost":   float(row["direct_material_cost"]) if row["direct_material_cost"] is not None else None,
                "depth":                  row["depth"],
                "children":               [],
            }
            node_map[str(row["part_id"])] = node

        # 부모-자식 연결
        for node in node_map.values():
            parent_id = node["parent_part_id"]
            if parent_id and parent_id in node_map:
                node_map[parent_id]["children"].append(node)

        # 루트 노드 수집
        root_nodes = [
            n for n in node_map.values()
            if n["parent_part_id"] is None
        ]

        if not root_nodes:
            return None

        # 루트가 여러 개인 경우(데이터 오염 방어): 첫 번째 사용
        root_node = root_nodes[0]

        # ------------------------------------------------------------------
        # 최종 응답 조립
        # [결정 #1] source_system, synced_at 추가
        # [결정 #2] only_confirmed 응답에 포함
        # ------------------------------------------------------------------
        return {
            "product_id":     str(product_row["product_id"]),
            "product_code":   product_row["product_code"],
            "product_name":   product_row["product_name"],
            "source_system":  product_row["source_system"],
            "synced_at":      str(product_row["synced_at"]) if product_row["synced_at"] else None,
            "bom_version":    product_row["version_number"],
            "bom_status":     product_row["status"],
            "only_confirmed": only_confirmed,
            "tree":           root_node,
        }
