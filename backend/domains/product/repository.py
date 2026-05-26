'''
crud.py 안에 get_bom_tree()함수만 존재. 이를 repository.py 파일로 옮기는 게 목표.

repository.py
└─ get_bom_tree()     ← 그대로 이동
└─ create_product()   ← 오늘 추가
└─ get_product()      ← 오늘 추가
└─ list_products()    ← 오늘 추가

router.py가 crud.py를 직접 import하고 있는 것도 수정 필요.
'''
# =============================================================================
# backend/domains/product/repository.py
#
# KIRA Compliance Intelligence Platform — Product Domain DB Query Layer
#
# 역할: Product 도메인의 모든 DB 입출력 담당.
#   - create_product : 제품 등록 INSERT
#   - get_product    : 제품 단건 조회
#   - list_products  : 제품 목록 조회
#   - get_bom_tree   : 5계층 BOM 트리 재귀 CTE 조회
#
# 계층 규칙 (PROJECT_CORE.md 5-1):
#   - router.py → service.py → repository.py 단방향 호출.
#   - 타 도메인 import 없음.
#   - 이벤트 발행 없음 (이벤트는 service.py 책임).
# =============================================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.product.models import Product
from backend.infrastructure.trace import trace_tool


class ProductRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # -----------------------------------------------------------------------
    # create_product
    # -----------------------------------------------------------------------

    @trace_tool("create_product")
    async def create_product(
        self,
        product_code: str,
        product_name: Optional[str] = None,
        manufacturer_id: Optional[UUID] = None,
        type: Optional[str] = None,
        specs: Optional[Dict[str, Any]] = None,
    ) -> Product:
        """
        제품을 DB에 INSERT하고 저장된 ORM 객체를 반환한다.

        [호출 흐름]
        router → service.create_product() → repository.create_product()

        [유효성 검사 책임 분리]
        비즈니스 규칙(hs_code 6자리 등) 검증은 service.py에서 처리.
        이 함수는 받은 값을 그대로 INSERT한다.

        [반환]
        저장된 Product ORM 객체 (product_id 포함).
        """
        product = Product(
            product_code=product_code,
            product_name=product_name,
            manufacturer_id=manufacturer_id,
            type=type,
            specs=specs,
        )
        self.session.add(product)
        await self.session.flush()    # product_id 확정 (commit은 service에서)
        await self.session.refresh(product)
        return product

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
        제품 목록을 생성일 내림차순으로 반환한다.

        [페이지네이션]
        limit / offset 기반. 기본 20건.

        [반환]
        Product ORM 객체 리스트.
        """
        result = await self.session.execute(
            select(Product)
            .order_by(Product.created_at.desc())
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
    ) -> Optional[Dict[str, Any]]:
        """
        product_id에 해당하는 제품의 active BOM 버전 기준
        5계층 BOM 트리를 반환한다.

        [쿼리 전략]
        1단계 — products + bom_versions 조회
            product_id로 제품 정보와 active BOM 버전을 가져온다.
            active 버전이 없으면 None 반환.

        2단계 — WITH RECURSIVE CTE
            bom_version_id 기준으로 bom_items에 속한 루트 부품
            (parent_part_id IS NULL)을 앵커로 잡고,
            parent_part_id 관계를 따라 전 계층을 플랫하게 조회한다.
            bom_items JOIN이 bom_version_id로 필터하므로
            다른 BOM의 부품이 섞이지 않는다.

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
            return None

        bom_version_id = product_row["bom_version_id"]

        # ------------------------------------------------------------------
        # 2단계: WITH RECURSIVE CTE — 전 계층 플랫 조회
        #
        # 앵커: bom_version에 속한 부품 중 parent_part_id IS NULL (루트)
        #       bom_items JOIN이 bom_version_id로 필터하므로
        #       다른 BOM의 루트가 섞이지 않는다.
        # 재귀: 방금 찾은 부품의 part_id = 다음 부품의 parent_part_id
        # ------------------------------------------------------------------
        recursive_query = text("""
            WITH RECURSIVE bom_tree AS (

                -- 앵커: 루트 부품 (parent_part_id IS NULL)
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
                    bi.direct_material_cost
                FROM parts p
                JOIN bom_items bi
                    ON bi.part_id        = p.part_id
                   AND bi.bom_version_id = :bom_version_id
                WHERE p.parent_part_id IS NULL

                UNION ALL

                -- 재귀: 직전 계층 부품의 자식 탐색
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
                    bi.direct_material_cost
                FROM parts p
                JOIN bom_items bi
                    ON bi.part_id        = p.part_id
                   AND bi.bom_version_id = :bom_version_id
                JOIN bom_tree bt
                    ON p.parent_part_id  = bt.part_id

            )
            SELECT * FROM bom_tree
            ORDER BY tier_level, part_code
        """)

        tree_result = await self.session.execute(
            recursive_query,
            {"bom_version_id": str(bom_version_id)},
        )
        rows = tree_result.mappings().all()

        if not rows:
            return None

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
                "children":               [],
            }
            node_map[str(row["part_id"])] = node

        # 부모-자식 연결
        for node in node_map.values():
            parent_id = node["parent_part_id"]
            if parent_id and parent_id in node_map:
                node_map[parent_id]["children"].append(node)

        # 루트 노드 (리스트로 수집 후 첫 번째 사용)
        root_nodes = [
            n for n in node_map.values()
            if n["parent_part_id"] is None
        ]

        if not root_nodes:
            return None

        root_node = root_nodes[0]

        # ------------------------------------------------------------------
        # 최종 응답 조립
        # ------------------------------------------------------------------
        return {
            "product_id":   str(product_row["product_id"]),
            "product_code": product_row["product_code"],
            "product_name": product_row["product_name"],
            "bom_version":  product_row["version_number"],
            "bom_status":   product_row["status"],
            "tree":         root_node,
        }


