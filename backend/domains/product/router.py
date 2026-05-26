# =============================================================================
# backend/domains/product/router.py
#
# KIRA Compliance Intelligence Platform — Product Domain Router
#
# 역할: Product 도메인의 HTTP 진입점 (얇은 라우팅 레이어).
#   - POST /products            : 제품 등록
#   - GET  /products            : 제품 목록 조회
#   - GET  /products/{id}       : 제품 단건 조회
#   - GET  /products/{id}/bom   : 5계층 BOM 트리 조회
#
# 계층 규칙 (PROJECT_CORE.md 5-1):
#   - service만 호출. repository·DB 직접 접근 금지.
#   - HTTP 수신·응답·파라미터 파싱만 담당.
#   - 상위 라우터(main.py)에서 prefix를 꽂아 최종 경로가 완성됨.
#     예: app.include_router(router, prefix="/api/v1/products")
# =============================================================================

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import get_db
from backend.domains.product import service

router = APIRouter(tags=["Product"])


# ---------------------------------------------------------------------------
# Request Schema
# ---------------------------------------------------------------------------

class ProductCreateRequest(BaseModel):
    product_code: str
    product_name: Optional[str] = None
    manufacturer_id: Optional[UUID] = None
    type: Optional[str] = None
    specs: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# POST /products — 제품 등록
# ---------------------------------------------------------------------------

@router.post(
    "",
    status_code=201,
    summary="제품 등록 (Create Product)",
    description="""
## 제품을 등록하고 ProductCreated 이벤트를 발행합니다.

### 비즈니스 규칙
- `product_code`는 UNIQUE — 중복 시 **409** 반환.

### 이벤트
- 등록 성공 시 `ProductCreated` 이벤트 발행.
""",
    responses={
        201: {"description": "제품 등록 성공"},
        409: {"description": "product_code 중복"},
        422: {"description": "요청 바디 유효성 오류"},
    },
)
async def create_product(
    body: ProductCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    return await service.create_product(
        db=db,
        product_code=body.product_code,
        product_name=body.product_name,
        manufacturer_id=body.manufacturer_id,
        type=body.type,
        specs=body.specs,
    )


# ---------------------------------------------------------------------------
# GET /products — 제품 목록
# ---------------------------------------------------------------------------

@router.get(
    "",
    summary="제품 목록 조회 (List Products)",
    description="""
## 등록된 제품 목록을 생성일 내림차순으로 반환합니다.

### 페이지네이션
- `limit` (기본 20) / `offset` (기본 0) 기반.
""",
    responses={
        200: {"description": "제품 목록 반환"},
    },
)
async def list_products(
    limit: int = Query(default=20, ge=1, le=100, description="최대 반환 건수"),
    offset: int = Query(default=0, ge=0, description="건너뛸 건수"),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    return await service.list_products(db=db, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# GET /products/{product_id} — 제품 단건
# ---------------------------------------------------------------------------

@router.get(
    "/{product_id}",
    summary="제품 단건 조회 (Get Product)",
    description="""
## product_id로 제품 정보를 반환합니다.

### 예외
- 존재하지 않는 product_id → **404**.
""",
    responses={
        200: {"description": "제품 정보 반환"},
        404: {"description": "제품 없음"},
        422: {"description": "유효하지 않은 UUID"},
    },
)
async def get_product(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    return await service.get_product(db=db, product_id=product_id)


# ---------------------------------------------------------------------------
# GET /products/{product_id}/bom — BOM 트리
# ---------------------------------------------------------------------------

@router.get(
    "/{product_id}/bom",
    response_class=JSONResponse,
    summary="제품 BOM 트리 조회 (Get BOM Tree)",
    description="""
## 5계층 BOM 트리를 반환합니다.

배터리 제품의 전체 자재명세서(BOM)를 **Pack → Module → Cell → 전구체 → 광물**
5계층 중첩 JSON 구조로 반환합니다.

### 조회 기준
- `status = 'active'` BOM 버전 기준.
- active BOM 버전이 없으면 **404** 반환.

### 반환 구조
```json
{
  "product_id": "UUID",
  "product_code": "BAT-NCM811-100Ah",
  "bom_version": "1.0",
  "bom_status": "active",
  "tree": {
    "tier_level": 1,
    "children": [...]
  }
}
```
""",
    responses={
        200: {"description": "BOM 트리 반환"},
        404: {"description": "제품 또는 active BOM 없음"},
        422: {"description": "유효하지 않은 UUID"},
    },
)
async def get_product_bom_tree(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    return await service.get_bom_tree(db=db, product_id=product_id)
