# =============================================================================
# backend/domains/product/crud.py
#
# KIRA Compliance Intelligence Platform — Product Domain CRUD / Query Layer
#
# 현재 구현 상태: DB 미연결 Mock Stub
#   - get_bom_tree()는 5계층 중첩 JSON을 하드코딩으로 반환.
#   - 실제 DB 연결 시 이 파일의 Mock 블록을 재귀 CTE 쿼리로 교체.
#
# 설계 원칙 (PROJECT_CORE.md 5-1 준수):
#   - 도메인 격리 — product 도메인 외부 import 없음.
#   - database.py Base 연결 인지 — 실제 쿼리 전환 시 AsyncSession inject.
#   - N차 트리 탐색은 재귀 CTE 사용 예정 (현재는 Mock으로 대체).
# =============================================================================

from __future__ import annotations

from typing import Any, Dict
from uuid import UUID


# ---------------------------------------------------------------------------
# get_bom_tree
# ---------------------------------------------------------------------------

def get_bom_tree(product_id: UUID) -> Dict[str, Any]:
    """
    5계층 BOM 트리를 반환한다.

    [현재 상태 — Mock Stub]
    실제 DB 조회(재귀 CTE) 구현 전까지 하드코딩된 가짜 데이터를 반환.
    product_id 인자는 수신만 하고 아직 쿼리에 사용되지 않음.

    [실제 DB 전환 시 교체 계획]
    1. AsyncSession을 인자로 추가: get_bom_tree(db: AsyncSession, product_id: UUID)
       from backend.infrastructure.database import AsyncSessionLocal
    2. Mock 반환 블록을 아래 재귀 CTE로 교체:

        WITH RECURSIVE bom_tree AS (
            -- 앵커: product_id 기준 active BOM 버전의 루트 부품
            SELECT p.*, bi.required_quantity, bi.required_quantity_unit,
                   bi.origin_country, bi.direct_material_cost, 0 AS depth
            FROM parts p
            JOIN bom_items bi ON bi.part_id = p.part_id
            JOIN bom_versions bv ON bv.bom_version_id = bi.bom_version_id
            WHERE bv.product_id = :product_id
              AND bv.status = 'active'
              AND p.parent_part_id IS NULL

            UNION ALL

            -- 재귀: 자식 부품 순회
            SELECT p.*, bi.required_quantity, bi.required_quantity_unit,
                   bi.origin_country, bi.direct_material_cost, bt.depth + 1
            FROM parts p
            JOIN bom_items bi ON bi.part_id = p.part_id
            JOIN bom_tree bt ON p.parent_part_id = bt.part_id
        )
        SELECT * FROM bom_tree ORDER BY depth, part_code;

    [반환 구조]
    {
        "product_id": str,
        "product_code": str,
        "product_name": str,
        "bom_version": str,
        "bom_status": str,          # BomVersionStatus: draft / active / deprecated
        "tree": {                   # 루트 노드 (tier_level=1, Pack)
            "part_id": str,
            "part_code": str,
            "part_name": str,
            "tier_level": int,      # 1=Pack / 2=Module / 3=Cell / 4=전구체 / 5=광물
            "parent_part_id": str | None,
            "hs_code": str,         # 6자리 이상 필수 (FTA CTC 판정 키)
            "material_type": str | None,
            "unit_price": float | None,
            "required_quantity": float,     # bom_items 소속
            "required_quantity_unit": str,  # bom_items 소속
            "origin_country": str,          # bom_items 소속 (ISO 3166-1 alpha-2)
            "direct_material_cost": float | None,  # bom_items 소속 (RVC 계산용)
            "children": [ ... ]     # 빈 배열이면 터미널 노드 (광물, tier_level=5)
        }
    }
    """

    # ------------------------------------------------------------------
    # Mock Data — 5계층 중첩 트리
    #
    # 시나리오: NCM811 배터리 팩 100Ah
    #   Tier 1 — Pack          : PACK-NCM811-100Ah    (원산지: KR)
    #   Tier 2 — Module        : MOD-NCM811-10S4P     (원산지: KR)
    #   Tier 3 — Cell          : CELL-NCM811-21700    (원산지: KR)
    #   Tier 4 — 전구체         : PRE-NCM811-CAM       (원산지: KR)
    #   Tier 5 — 광물(코발트)   : MIN-CO-DRC-001       (원산지: CD ← DRC, UFLPA 주의)
    #   Tier 5 — 광물(니켈)     : MIN-NI-PH-001        (원산지: PH)
    #   Tier 5 — 광물(리튬)     : MIN-LI-CL-001        (원산지: CL ← 칠레)
    # ------------------------------------------------------------------

    return {
        "product_id": str(product_id),
        "product_code": "BAT-NCM811-100Ah",
        "product_name": "NCM811 배터리 팩 100Ah",
        "bom_version": "v1.0",
        "bom_status": "active",
        "tree": {
            # --------------------------------------------------------
            # Tier 1 — Pack
            # --------------------------------------------------------
            "part_id": "a1000000-0000-0000-0000-000000000001",
            "part_code": "PACK-NCM811-100Ah",
            "part_name": "NCM811 배터리 팩",
            "tier_level": 1,
            "parent_part_id": None,
            "hs_code": "850760",
            "material_type": None,
            "unit_price": 850000.0,
            "required_quantity": 1.0,
            "required_quantity_unit": "개",
            "origin_country": "KR",
            "direct_material_cost": 720000.0,
            "children": [
                {
                    # ------------------------------------------------
                    # Tier 2 — Module
                    # ------------------------------------------------
                    "part_id": "a2000000-0000-0000-0000-000000000001",
                    "part_code": "MOD-NCM811-10S4P",
                    "part_name": "NCM811 모듈 10S4P",
                    "tier_level": 2,
                    "parent_part_id": "a1000000-0000-0000-0000-000000000001",
                    "hs_code": "850760",
                    "material_type": None,
                    "unit_price": 180000.0,
                    "required_quantity": 4.0,
                    "required_quantity_unit": "개",
                    "origin_country": "KR",
                    "direct_material_cost": 152000.0,
                    "children": [
                        {
                            # ----------------------------------------
                            # Tier 3 — Cell
                            # ----------------------------------------
                            "part_id": "a3000000-0000-0000-0000-000000000001",
                            "part_code": "CELL-NCM811-21700",
                            "part_name": "NCM811 원통형 셀 21700",
                            "tier_level": 3,
                            "parent_part_id": "a2000000-0000-0000-0000-000000000001",
                            "hs_code": "850760",
                            "material_type": "NCM 811",
                            "unit_price": 4200.0,
                            "required_quantity": 40.0,
                            "required_quantity_unit": "개",
                            "origin_country": "KR",
                            "direct_material_cost": 3800.0,
                            "children": [
                                {
                                    # ------------------------------------
                                    # Tier 4 — 전구체 (양극재)
                                    # ------------------------------------
                                    "part_id": "a4000000-0000-0000-0000-000000000001",
                                    "part_code": "PRE-NCM811-CAM",
                                    "part_name": "NCM811 양극재 전구체",
                                    "tier_level": 4,
                                    "parent_part_id": "a3000000-0000-0000-0000-000000000001",
                                    "hs_code": "282739",
                                    "material_type": "NCM 전구체",
                                    "unit_price": 28000.0,
                                    "required_quantity": 0.85,
                                    "required_quantity_unit": "kg",
                                    "origin_country": "KR",
                                    "direct_material_cost": 25000.0,
                                    "children": [
                                        {
                                            # --------------------------
                                            # Tier 5 — 광물: 코발트
                                            # origin_country = "CD" (콩고민주공화국)
                                            # → Geo Audit Agent 고위험 지역 판정 대상
                                            # → UFLPA 규제 모니터링 대상
                                            # --------------------------
                                            "part_id": "a5000000-0000-0000-0000-000000000001",
                                            "part_code": "MIN-CO-DRC-001",
                                            "part_name": "코발트 원광",
                                            "tier_level": 5,
                                            "parent_part_id": "a4000000-0000-0000-0000-000000000001",
                                            "hs_code": "260500",
                                            "material_type": "코발트",
                                            "unit_price": 38000.0,
                                            "required_quantity": 0.18,
                                            "required_quantity_unit": "kg",
                                            "origin_country": "CD",
                                            "direct_material_cost": None,
                                            "children": [],  # 터미널 노드
                                        },
                                        {
                                            # --------------------------
                                            # Tier 5 — 광물: 니켈
                                            # origin_country = "PH" (필리핀)
                                            # --------------------------
                                            "part_id": "a5000000-0000-0000-0000-000000000002",
                                            "part_code": "MIN-NI-PH-001",
                                            "part_name": "니켈 원광",
                                            "tier_level": 5,
                                            "parent_part_id": "a4000000-0000-0000-0000-000000000001",
                                            "hs_code": "260400",
                                            "material_type": "니켈",
                                            "unit_price": 21000.0,
                                            "required_quantity": 0.08,
                                            "required_quantity_unit": "kg",
                                            "origin_country": "PH",
                                            "direct_material_cost": None,
                                            "children": [],  # 터미널 노드
                                        },
                                        {
                                            # --------------------------
                                            # Tier 5 — 광물: 리튬
                                            # origin_country = "CL" (칠레)
                                            # --------------------------
                                            "part_id": "a5000000-0000-0000-0000-000000000003",
                                            "part_code": "MIN-LI-CL-001",
                                            "part_name": "리튬 원광",
                                            "tier_level": 5,
                                            "parent_part_id": "a4000000-0000-0000-0000-000000000001",
                                            "hs_code": "260190",
                                            "material_type": "리튬",
                                            "unit_price": 15000.0,
                                            "required_quantity": 0.07,
                                            "required_quantity_unit": "kg",
                                            "origin_country": "CL",
                                            "direct_material_cost": None,
                                            "children": [],  # 터미널 노드
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    }


# =============================================================================
# 로컬 단독 실행 테스트 (도커 불필요)
# 실행: python backend/domains/product/crud.py
# =============================================================================

if __name__ == "__main__":
    import json
    from pprint import pprint
    from uuid import uuid4

    print("=" * 60)
    print("  KIRA — get_bom_tree() Mock Stub 로컬 테스트")
    print("=" * 60)

    # 임시 product_id (실제 DB 없이 아무 UUID나 사용 가능)
    test_product_id = uuid4()
    print(f"\n▶ 입력 product_id : {test_product_id}\n")

    result = get_bom_tree(product_id=test_product_id)

    # ------------------------------------------------------------------
    # 1) pprint — 딕셔너리 구조 확인
    # ------------------------------------------------------------------
    print("[ pprint 출력 ]")
    pprint(result, width=100, sort_dicts=False)

    # ------------------------------------------------------------------
    # 2) JSON 직렬화 — 프론트엔드 전달 형태 확인
    # ------------------------------------------------------------------
    print("\n[ JSON 직렬화 출력 ]")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # ------------------------------------------------------------------
    # 3) 간단한 구조 검증
    # ------------------------------------------------------------------
    print("\n[ 구조 검증 ]")

    tree = result["tree"]
    assert tree["tier_level"] == 1,          "루트는 tier_level=1 (Pack) 이어야 함"
    assert tree["parent_part_id"] is None,   "루트의 parent_part_id는 None 이어야 함"

    module = tree["children"][0]
    assert module["tier_level"] == 2,        "2계층은 Module 이어야 함"

    cell = module["children"][0]
    assert cell["tier_level"] == 3,          "3계층은 Cell 이어야 함"

    precursor = cell["children"][0]
    assert precursor["tier_level"] == 4,     "4계층은 전구체 이어야 함"

    minerals = precursor["children"]
    assert len(minerals) == 3,               "광물 노드는 3개(Co/Ni/Li) 이어야 함"

    for mineral in minerals:
        assert mineral["tier_level"] == 5,   "5계층은 광물 이어야 함"
        assert mineral["children"] == [],    "광물 노드는 터미널(children=[]) 이어야 함"
        assert len(mineral["hs_code"]) >= 6, "HS Code는 6자리 이상 이어야 함"

    # origin_country 확인 — 코발트는 CD (DRC 고위험 지역)
    cobalt = next(m for m in minerals if m["part_code"] == "MIN-CO-DRC-001")
    assert cobalt["origin_country"] == "CD", "코발트 원산지는 CD (콩고민주공화국) 이어야 함"

    print("  ✅ 모든 검증 통과")
    print(f"  ✅ 5계층 트리 구조 정상 (Pack→Module→Cell→전구체→광물)")
    print(f"  ✅ 광물 노드 3개 모두 터미널 확인 (children=[])")
    print(f"  ✅ HS Code 6자리 이상 확인")
    print(f"  ✅ 코발트 원산지 CD (Geo Audit 고위험 지역 판정 대상) 확인")
    print("\n  → DB 연결 후 이 Mock 블록을 재귀 CTE 쿼리로 교체하세요.")
    print("=" * 60)
