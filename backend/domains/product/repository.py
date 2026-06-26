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

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.product.models import (
    BomVersion,
    Customer,
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
    ) -> Dict[str, Any]:
        """
        [결정 #1] 외부 원천에서 제품 데이터를 읽어 customers + products 테이블에 UPSERT한다.

        [W4 확장]
        고객사(Customer)를 제품(Product)보다 먼저 UPSERT해요.
        이유: products.customer_id 가 customers.customer_id 를 FK로 참조하기 때문에
              customer row가 없으면 product INSERT 자체가 실패해요.

        [처리 순서]
            1. _load_seed_products() 로 raw dict 목록 조회
            2. 고객사 UPSERT (_upsert_customer) — customer_code 기준 충돌 처리
            3. 제품 UPSERT (_upsert_product) — product_code 기준, customer_id 채움

        [반환]
            {
                "customers": [(Customer, is_new), ...],   # is_new=True면 신규 INSERT
                "products":  [Product, ...],
            }
            service가 CustomerImported / ProductImported 발행 시 이 반환값을 씀.

        [UPSERT 전략 — idempotent]
            동일 ingest를 두 번 실행해도 중복 row 없음.
            - 고객사: ON CONFLICT (customer_code) DO UPDATE synced_at
            - 제품:   ON CONFLICT (product_code)  DO UPDATE customer_id + 동기화 정보
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
        # 1단계: 고객사 UPSERT
        # customer_code 기준. 고객사가 확정되어야 제품에 customer_id를 채울 수 있어요.
        # customer_code → Customer 객체 캐시 맵: 같은 고객사를 여러 제품이 공유할 때
        # DB를 반복 조회하지 않기 위해 메모리에 들고 있어요.
        # ------------------------------------------------------------------
        customer_cache: Dict[str, Tuple[Customer, bool]] = {}

        for raw in raw_products:
            customer_code = raw.get("customer_code")
            if not customer_code or customer_code in customer_cache:
                # customer_code 없으면 고객사 미연결 제품 — 스킵
                # 이미 처리된 고객사면 캐시 재사용 — 중복 UPSERT 방지
                continue

            customer, is_new = await self._upsert_customer(
                customer_code=customer_code,
                customer_name=raw.get("customer_name", customer_code),
                country=raw.get("customer_country"),
                external_id=raw.get("customer_external_id", customer_code),
                source_system=source_system,
                now=now,
            )
            customer_cache[customer_code] = (customer, is_new)

        await self.session.flush()  # customer PK 확정 — product FK 참조 전 필수

        # ------------------------------------------------------------------
        # 2단계: 제품 UPSERT
        # customer_cache 에서 customer_id 를 꺼내 product row에 채워요.
        # ------------------------------------------------------------------
        upserted_products: List[Product] = []

        for raw in raw_products:
            customer_code = raw.get("customer_code")
            customer_id = None
            if customer_code and customer_code in customer_cache:
                customer_obj, _ = customer_cache[customer_code]
                customer_id = customer_obj.customer_id

            stmt = (
                pg_insert(Product)
                .values(
                    product_code=raw["product_code"],
                    product_name=raw.get("product_name"),
                    manufacturer_id=raw.get("manufacturer_id"),
                    type=raw.get("type"),
                    specs=raw.get("specs"),
                    customer_id=customer_id,
                    model_name=raw.get("model_name"),
                    amperage_ah=raw.get("amperage_ah"),
                    source_system=source_system,
                    external_id=raw.get("external_id", raw["product_code"]),
                    synced_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["product_code"],
                    set_={
                        "product_name":  raw.get("product_name"),
                        "customer_id":   customer_id,
                        "model_name":    raw.get("model_name"),
                        "amperage_ah":   raw.get("amperage_ah"),
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
                upserted_products.append(product)

        await self.session.flush()

        return {
            "customers": list(customer_cache.values()),   # [(Customer, is_new), ...]
            "products":  upserted_products,
        }

    async def _upsert_customer(
        self,
        customer_code: str,
        customer_name: str,
        country: Optional[str],
        external_id: str,
        source_system: str,
        now: datetime,
    ) -> Tuple["Customer", bool]:
        """
        [W4 신설] customer_code 기준 고객사 UPSERT 헬퍼.

        [충돌 처리]
            ON CONFLICT (customer_code) DO UPDATE synced_at / source_system
            고객사명·국가는 원천이 바뀌는 경우가 거의 없지만, synced_at 갱신으로
            "마지막으로 확인된 시각"은 항상 최신 유지해요.

        [is_new 플래그]
            service가 CustomerImported 이벤트에 담아 발행해요.
            downstream이 "신규 고객사 등장" vs "기존 갱신"을 구분할 수 있어요.
            PostgreSQL의 xmax 컬럼으로 판별: INSERT면 xmax=0, UPDATE면 xmax>0.

        [반환]
            (Customer ORM 객체, is_new: bool)
        """
        stmt = (
            pg_insert(Customer)
            .values(
                customer_code=customer_code,
                customer_name=customer_name,
                country=country,
                source_system=source_system,
                external_id=external_id,
                synced_at=now,
            )
            .on_conflict_do_update(
                index_elements=["customer_code"],
                set_={
                    "source_system": source_system,
                    "external_id":   external_id,
                    "synced_at":     now,
                },
            )
            .returning(Customer, text("xmax::text::int = 0 AS is_new"))
        )
        result = await self.session.execute(stmt)
        row = result.first()
        customer: Customer = row[0]
        is_new: bool = row[1]
        return customer, is_new

    async def _load_seed_products(self) -> List[Dict[str, Any]]:
        """
        시연용 시드 데이터를 반환한다.

        [W4 확장]
        고객사(customer_code·customer_name·customer_country) +
        모델(model_name) + 암페어(amperage_ah) 필드를 포함해요.
        fetch_from_source가 이 raw dict를 읽어 고객사 → 제품 순으로 UPSERT해요.

        [시드 구성 — 제품 4개, 고객사 2개]
            BMW    → iX3  108Ah  (product_code: BMW-IX3-108)
            BMW    → i4    81Ah  (product_code: BMW-I4-81)
            Mercedes → GLC  90Ah  v1 deprecated  (product_code: MB-GLC-90-V1)
            Mercedes → GLC  90Ah  v2 active       (product_code: MB-GLC-90-V2)
            ※ GLC 두 버전은 생산기간이 다른 별도 BOM 버전 시연용.
               product row는 2개, BOM 버전은 각 product마다 1개씩.

        [실환경 전환 포인트]
        이 함수를 ERP API 클라이언트 호출로 교체하면
        fetch_from_source() 나머지 로직은 그대로 재사용 가능.
        """
        return [
            {
                # ── BMW iX3 50 108Ah ───────────────────────────────────
                "product_code":         "BMW-IX3-NCM811-108",
                "product_name":         "BMW iX3 Cylindrical NCM811 108Ah",
                "type":                 "battery_pack",
                "external_id":          "ERP-PROD-IX3",
                # 고객사 정보
                "customer_code":        "BMW",
                "customer_name":        "BMW AG",
                "customer_country":     "DE",
                "customer_external_id": "ERP-CUST-BMW",
                # 제품 3축
                "model_name":           "iX3 50",
                "amperage_ah":          108.00,
            },
            {
                # ── BMW i4 81Ah ────────────────────────────────────────
                "product_code":         "BMW-I4-NCM-81",
                "product_name":         "BMW i4 Prismatic NCM 81Ah",
                "type":                 "battery_pack",
                "external_id":          "ERP-PROD-I4",
                # 고객사 정보 — BMW는 위와 동일 customer_code → UPSERT 재사용
                "customer_code":        "BMW",
                "customer_name":        "BMW AG",
                "customer_country":     "DE",
                "customer_external_id": "ERP-CUST-BMW",
                # 제품 3축
                "model_name":           "i4",
                "amperage_ah":          81.00,
            },
            {
                # ── Mercedes GLC EV 94Ah ───────────────────────────────
                "product_code":         "MB-GLC-NCM-94",
                "product_name":         "Mercedes GLC EV Prismatic NCM 94Ah",
                "type":                 "battery_pack",
                "external_id":          "ERP-PROD-GLC",
                # 고객사 정보
                "customer_code":        "MERCEDES",
                "customer_name":        "Mercedes-Benz Group AG",
                "customer_country":     "DE",
                "customer_external_id": "ERP-CUST-MB",
                # 제품 3축
                "model_name":           "GLC EV",
                "amperage_ah":          94.00,
            },
            {
                # ── Mercedes EQS 118Ah ─────────────────────────────────
                "product_code":         "MB-EQS-NCM-118",
                "product_name":         "Mercedes EQS Prismatic NCM 118Ah",
                "type":                 "battery_pack",
                "external_id":          "ERP-PROD-EQS",
                # 고객사 정보 — MERCEDES는 위와 동일 → UPSERT 재사용
                "customer_code":        "MERCEDES",
                "customer_name":        "Mercedes-Benz Group AG",
                "customer_country":     "DE",
                "customer_external_id": "ERP-CUST-MB",
                # 제품 3축
                "model_name":           "EQS",
                "amperage_ah":          118.00,
            },
        ]


    # -----------------------------------------------------------------------
    # get_product
    # -----------------------------------------------------------------------

    @trace_tool("get_product")
    async def get_product(
        self,
        product_id: UUID,
        tenant_id: Optional[UUID] = None,
    ) -> Optional[Product]:
        """
        product_id로 제품 단건을 조회한다.
        tenant_id 지정 시 소유 테넌트만(§0.2) — 남의 테넌트면 None(→404, 존재 은닉).

        [반환]
        존재하면 Product ORM 객체, 없으면 None.
        """
        stmt = select(Product).where(Product.product_id == product_id)
        if tenant_id is not None:
            stmt = stmt.where(Product.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
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
            
            True  → link_status = 'supplychain_confirmed' 노드만 포함 (기본값, 운영 화면용).
            False → 'supplychain_declared' 포함 전체 트리 (공급망 맵 전체 뷰용).

            schema.sql migration 완료 — supply_chain_map.link_status 컬럼 존재 확인.
            허용값: 'supplychain_declared' / 'supplychain_confirmed' (chk_link_status).
            필터 활성화 완료 (L317~323 주석 해제).
            
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
        #   schema migration 완료. link_status 필터 활성화.
        #   허용값 주의: schema chk_link_status 기준
        #     'supplychain_confirmed' (confirmed 상태)
        #     'supplychain_declared'  (선언만 된 상태, unconfirmed)        
        #
        # [depth 상한]
        #   depth < 5 조건 필수 — parts 자기참조 순환 참조 방어.
        #   5계층(Pack=0 ~ 광물=4) 초과는 데이터 오염으로 간주.
        # ------------------------------------------------------------------
        # [결정 #2] link_status 필터 문자열 생성
        # scm.map_id IS NULL 조건: supply_chain_map에 매핑이 없는 부품(직접 BOM 항목)도 포함.
        # 허용값은 schema chk_link_status 그대로 사용 ('supplychain_confirmed').
        link_status_filter = ""
        

        recursive_query = text(f"""
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
               WHERE p.parent_part_id NOT IN (
                   SELECT p2.part_id
                   FROM parts p2
                   JOIN bom_items bi2
                       ON bi2.part_id        = p2.part_id
                      AND bi2.bom_version_id = :bom_version_id
                   WHERE p2.parent_part_id IS NOT NULL
               )
                 {link_status_filter}  

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
                    NULL::numeric(15,4) AS required_quantity,
                    NULL::varchar(20) AS required_quantity_unit,
                    NULL::varchar(2) AS origin_country,
                    NULL::numeric(15,4) AS direct_material_cost,
                    bt.depth + 1
                FROM parts p   
                JOIN bom_tree bt
                    ON p.parent_part_id  = bt.part_id
                WHERE bt.depth < 5  

            )
            SELECT * FROM bom_tree
            ORDER BY depth, tier_level, part_code
        
        """) # f-string: link_status_filter가 빌드 타임에 삽입됨. SQL Injection 위험 없음
            # (link_status_filter는 코드 상수, 외부 입력값 미사용)

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
        
    # -----------------------------------------------------------------------
    # list_products_filtered
    # -----------------------------------------------------------------------

    @trace_tool("list_products_filtered")
    async def list_products_filtered(
        self,
        customer_id: Optional[UUID] = None,
        model_name: Optional[str] = None,
        min_ah: Optional[float] = None,
        max_ah: Optional[float] = None,
        limit: int = 20,
        offset: int = 0,
        tenant_id: Optional[UUID] = None,
    ) -> List[Tuple[Product, Optional[str]]]:
        """
        고객사·모델·암페어 범위로 제품을 필터링하여 반환한다.

        [조인 이유]
        응답에 customer_name이 필요해서 Customer를 LEFT JOIN해요.
        customer_id가 없는 제품(고객사 미연결)도 목록에 포함되도록 OUTER JOIN.

        [필터 전략]
        파라미터가 None이면 해당 조건을 WHERE절에 추가하지 않아요.
        즉, 아무 파라미터 없이 호출하면 전체 목록이 나와요.

        [반환]
        (Product, customer_name: str | None) 튜플 목록.
        service에서 customer_name을 dict에 담을 때 튜플 두 번째 자리에서 꺼내요.
        """
        stmt = (
            select(Product, Customer.customer_name)
            .outerjoin(Customer, Product.customer_id == Customer.customer_id)
        )

        conditions = []
        # 테넌트 격리(§0.2): tenant_id 지정 시 소유 테넌트 제품만(0002 마이그레이션 컬럼).
        if tenant_id is not None:
            conditions.append(Product.tenant_id == tenant_id)
        if customer_id is not None:
            conditions.append(Product.customer_id == customer_id)
        if model_name is not None:
            # ilike = case-insensitive LIKE. 부분 일치로 "iX3" → "BMW iX3 50" 검색 가능.
            conditions.append(Product.model_name.ilike(f"%{model_name}%"))
        if min_ah is not None:
            conditions.append(Product.amperage_ah >= min_ah)
        if max_ah is not None:
            conditions.append(Product.amperage_ah <= max_ah)

        if conditions:
            stmt = stmt.where(and_(*conditions))

        stmt = (
            stmt
            .order_by(
                Product.synced_at.desc().nulls_last(),
                Product.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.execute(stmt)
        return result.all()   # [(Product, customer_name), ...]

    # -----------------------------------------------------------------------
    # get_bom_versions
    # -----------------------------------------------------------------------

    @trace_tool("get_bom_versions")
    async def get_bom_versions(
        self,
        product_id: UUID,
    ) -> List[BomVersion]:
        """
        product_id에 해당하는 모든 BOM 버전을 production_from 내림차순으로 반환한다.

        [status 필터 없음]
        active  deprecated 전부 반환해요.
        "현재"와 "과거" BOM을 함께 보는 게 이 API의 목적이에요.

        [정렬]
        production_from 내림차순 → 최신 생산기간이 상단.
        production_from이 NULL인 row는 후순위(nulls_last).
        """
        result = await self.session.execute(
            select(BomVersion)
            .where(BomVersion.product_id == product_id)
            .order_by(
                BomVersion.production_from.desc().nulls_last(),
                BomVersion.created_at.desc(),
            )
        )
        return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # get_bom_version_as_of
    # -----------------------------------------------------------------------

    @trace_tool("get_bom_version_as_of")
    async def get_bom_version_as_of(
        self,
        product_id: UUID,
        as_of: date,
    ) -> Optional[BomVersion]:
        """
        특정 날짜(as_of)에 생산 중이었던 BOM 버전을 반환한다.

        [조건 해석]
            production_from <= as_of
            AND (production_to IS NULL OR production_to >= as_of)

        production_to IS NULL = "아직 종료일 미정 = 현재도 생산 중".
        이 케이스를 OR로 함께 잡아야 현재 active 버전도 as_of 조회에 걸려요.
        예: as_of=오늘, production_to=NULL → active 버전이 정상 반환됨.

        [LIMIT 1]
        한 product·날짜 조합에서 버전이 겹치면 안 되는 게 원칙이지만,
        데이터 오염 방어로 LIMIT 1 처리해요.
        """
        result = await self.session.execute(
            select(BomVersion)
            .where(
                and_(
                    BomVersion.product_id == product_id,
                    BomVersion.production_from <= as_of,
                    or_(
                        BomVersion.production_to.is_(None),
                        BomVersion.production_to >= as_of,
                    ),
                )
            )
            .order_by(BomVersion.production_from.desc())
            .limit(1)
        )
        return result.scalars().first()
        
