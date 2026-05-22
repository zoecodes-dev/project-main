# =============================================================================
# backend/domains/product/router.py
#
# KIRA Compliance Intelligence Platform — Product Domain Router
#
# 역할: Product 도메인의 HTTP 진입점 (얇은 라우팅 레이어)
#   - 비즈니스 로직은 crud.py에 위임. 이 파일은 HTTP 수신/응답만 담당.
#   - 상위 라우터(main.py)에서 prefix를 꽂아 최종 경로가 완성됨.
#     예: app.include_router(router, prefix="/api/v1/products")
#         → 최종 경로: GET /api/v1/products/{product_id}/bom
#
# 도메인 격리 원칙 (PROJECT_CORE.md 5-1):
#   - 타 도메인 import 없음.
#   - crud.py 하나만 참조.
# =============================================================================

from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import JSONResponse

from backend.domains.product.crud import get_bom_tree

router = APIRouter(tags=["Product"])


# ---------------------------------------------------------------------------
# GET /{product_id}/bom
# ---------------------------------------------------------------------------

@router.get(
    "/{product_id}/bom",
    response_class=JSONResponse,
    summary="제품 BOM 트리 조회 (Get BOM Tree)",
    description="""
## 5계층 BOM 트리를 반환합니다.

배터리 제품의 전체 자재명세서(BOM)를 **Pack → Module → Cell → 전구체 → 광물**
5계층 중첩 JSON 구조로 반환합니다.

### 반환 구조
```
{
  "product_id": "UUID",
  "product_code": "BAT-NCM811-100Ah",
  "bom_version": "v1.0",
  "bom_status": "active",
  "tree": {
    "tier_level": 1,        ← Pack (루트)
    "children": [{
      "tier_level": 2,      ← Module
      "children": [{
        "tier_level": 3,    ← Cell
        "children": [{
          "tier_level": 4,  ← 전구체
          "children": [
            { "tier_level": 5, "children": [] },  ← 광물 (터미널)
            ...
          ]
        }]
      }]
    }]
  }
}
```

### 각 노드의 주요 필드
| 필드 | 출처 | 설명 |
|------|------|------|
| `part_code` | parts | 원청 기준 부품 코드 |
| `hs_code` | parts | HS Code (6자리 이상, FTA CTC 판정 키) |
| `unit_price` | parts | 단가 (RVC 부가가치기준 FTA 계산용) |
| `origin_country` | bom_items | 원산지 국가 코드 ISO 3166-1 alpha-2 |
| `required_quantity` | bom_items | 소요 수량 |
| `direct_material_cost` | bom_items | 직접재료비 (RVC 계산용) |

### ⚠️ 현재 구현 상태
현재는 **Mock Stub** 단계입니다. DB 연결 없이 하드코딩된 샘플 데이터를 반환합니다.
실제 DB 재귀 CTE 쿼리 연결은 인프라 레이어 완성 후 `crud.py`에서 교체됩니다.
""",
    response_description="5계층 중첩 BOM 트리 JSON",
    responses={
        200: {
            "description": "BOM 트리 정상 반환",
            "content": {
                "application/json": {
                    "example": {
                        "product_id": "550e8400-e29b-41d4-a716-446655440000",
                        "product_code": "BAT-NCM811-100Ah",
                        "product_name": "NCM811 배터리 팩 100Ah",
                        "bom_version": "v1.0",
                        "bom_status": "active",
                        "tree": {
                            "part_code": "PACK-NCM811-100Ah",
                            "part_name": "NCM811 배터리 팩",
                            "tier_level": 1,
                            "hs_code": "850760",
                            "origin_country": "KR",
                            "children": ["..."]
                        }
                    }
                }
            }
        },
        422: {
            "description": "유효하지 않은 UUID 형식",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "loc": ["path", "product_id"],
                                "msg": "value is not a valid uuid",
                                "type": "type_error.uuid"
                            }
                        ]
                    }
                }
            }
        },
    },
)
async def get_product_bom_tree(
    product_id: UUID = Path(
        ...,
        description="조회할 제품의 UUID. 예: `550e8400-e29b-41d4-a716-446655440000`",
        example="550e8400-e29b-41d4-a716-446655440000",
    ),
) -> Dict[str, Any]:
    """
    product_id에 해당하는 제품의 5계층 BOM 트리를 반환한다.

    [현재 상태 — Mock Stub]
    product_id 수신 후 crud.get_bom_tree()에 위임.
    실제 DB 조회는 crud.py 교체 후 자동 반영되며 이 함수는 변경 불필요.
    """
    result = get_bom_tree(product_id=product_id)
    return result
