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
#   - 커밋은 service에서 일원화한다. ★ router에서 db.commit() 하지 않는다.
#   - 상위 라우터(main.py)에서 prefix를 꽂아 최종 경로가 완성됨.
#     예: app.include_router(router, prefix="/api/v1/products")
# =============================================================================

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import get_db
from backend.domains.product import service
from backend.domains.product.models import (
    ProductCreateRequest,
    ProductBrief,
    BomTreeResponse,
)

router = APIRouter(prefix="/products", tags=["Product"])


# ---------------------------------------------------------------------------
# POST /products — 제품 등록
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_product_endpoint(
    request: ProductCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """제품 등록 및 ProductCreated 이벤트 발행. (커밋·발행은 service가 처리)"""
    # ★ 여기서 db.commit() 하지 않는다 — service가 이미 커밋
    return await service.create_product(
        db=db,
        product_code=request.product_code,
        product_name=request.product_name,
        manufacturer_id=request.manufacturer_id,
        type=request.type,
        specs=request.specs,
    )


# ---------------------------------------------------------------------------
# GET /products — 제품 목록
# ---------------------------------------------------------------------------

@router.get("", response_model=List[ProductBrief])
async def list_products_endpoint(
    limit: int = Query(default=20, ge=1, le=100, description="최대 반환 건수"),
    offset: int = Query(default=0, ge=0, description="건너뛸 건수"),
    db: AsyncSession = Depends(get_db),
):
    """제품 목록 조회 (생성일 내림차순, limit/offset 페이지네이션)."""
    return await service.list_products(db=db, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# GET /products/{product_id} — 제품 단건
# ---------------------------------------------------------------------------

@router.get("/{product_id}", response_model=ProductBrief)
async def get_product_endpoint(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """제품 단건 조회. 존재하지 않는 product_id → 404."""
    product = await service.get_product(db=db, product_id=product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product  # response_model(ProductBrief)이 ORM→스키마 변환


# ---------------------------------------------------------------------------
# GET /products/{product_id}/bom — BOM 트리
# ---------------------------------------------------------------------------

@router.get("/{product_id}/bom", response_model=BomTreeResponse)
async def get_product_bom_tree_endpoint(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    5계층 BOM 트리 조회 (Pack → Module → Cell → 전구체 → 광물).
    active BOM 버전 기준. active 버전 없으면 404.
    """
    bom_tree = await service.get_bom_tree(db=db, product_id=product_id)
    if not bom_tree:
        raise HTTPException(status_code=404, detail="Product or active BOM not found")
    return bom_tree
