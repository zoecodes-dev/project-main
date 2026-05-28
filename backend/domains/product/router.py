# =============================================================================
# backend/domains/product/router.py
#
# KIRA Compliance Intelligence Platform — Product Domain Router
#
# 역할: Product 도메인의 HTTP 진입점 (얇은 라우팅 레이어).
#   - POST /products            : 외부 원천 동기화 트리거    ← 결정 #1
#   - GET  /products            : 제품 목록 조회
#   - GET  /products/{id}       : 제품 단건 조회
#   - GET  /products/{id}/bom   : 5계층 BOM 트리 조회       ← 결정 #2 only_confirmed
#   - POST /products/bom-versions/{id}/activate   : BOM 버전 활성화
#   - POST /products/bom-versions/{id}/deprecate  : BOM 버전 deprecated 전이
#
# [결정 #1 반영]
#   POST /products 는 제품 생성 폼이 아니라 "동기화 트리거" 엔드포인트.
#   요청 바디: ProductImportTrigger { source_system: "SEED" }
#   응답: 202 Accepted (비동기 처리 원칙 — PROJECT_CORE.md 5-7)
#
# [결정 #2 반영]
#   GET /products/{id}/bom 에 only_confirmed 쿼리 파라미터 추가.
#   기본값 True — 운영 화면에서는 confirmed 링크만 표시.
#
# 계층 규칙 (PROJECT_CORE.md 5-1):
#   - service만 호출. repository·DB 직접 접근 금지.
#   - HTTP 수신·응답·파라미터 파싱만 담당.
#   - db.commit() 금지 — 커밋은 service에서 일원화.
#   - 404 중복 처리 금지 — service가 이미 HTTPException을 발생시킴.
#   - 상위 라우터(main.py)에서 prefix를 꽂아 최종 경로가 완성됨.
#     예: app.include_router(router, prefix="/api/v1/products")
# =============================================================================

from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.product import service
from backend.domains.product.models import (
    BomTreeResponse,
    ProductBrief,
    ProductImportTrigger,
)
from backend.infrastructure.database import get_db

router = APIRouter(prefix="/products", tags=["Product"])


# ---------------------------------------------------------------------------
# POST /products — 외부 원천 동기화 트리거
# ---------------------------------------------------------------------------

@router.post("", status_code=202)
async def trigger_import_endpoint(
    request: ProductImportTrigger,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    [결정 #1] 외부 원천에서 제품 데이터를 동기화한다.

    이 엔드포인트는 "제품 생성 폼"이 아니다.
    ERP/MES/시드에서 데이터를 읽어 UPSERT하는 동기화 트리거.

    시연:  source_system="SEED" → DB 시드 데이터를 원천으로 ingest.
    실환경: source_system="ERP" → ERP API 호출로 교체 (repository 내부만).

    응답 202 Accepted:
        비동기 처리 원칙(PROJECT_CORE.md 5-7)에 따라 202 반환.
        { synced_count, source_system, products: [...] }
    """
    return await service.import_products(
        db=db,
        source_system=request.source_system,
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
    """제품 목록 조회 (synced_at 내림차순, limit/offset 페이지네이션)."""
    return await service.list_products(db=db, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# GET /products/{product_id} — 제품 단건
# ---------------------------------------------------------------------------

@router.get("/{product_id}", response_model=ProductBrief)
async def get_product_endpoint(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    제품 단건 조회.
    존재하지 않는 product_id → 404 (service에서 처리, 여기서 중복 체크 안 함).
    """
    return await service.get_product(db=db, product_id=product_id)


# ---------------------------------------------------------------------------
# GET /products/{product_id}/bom — BOM 트리
# ---------------------------------------------------------------------------

@router.get("/{product_id}/bom", response_model=BomTreeResponse)
async def get_product_bom_tree_endpoint(
    product_id: UUID,
    only_confirmed: bool = Query(
        default=True,
        description=(
            "[결정 #2] supply_chain_map.link_status 필터. "
            "True=confirmed 링크만(기본값, 운영 화면용) / "
            "False=pending 포함 전체 트리(공급망 맵 전체 뷰용)."
        ),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    5계층 BOM 트리 조회 (Pack → Module → Cell → 전구체 → 광물).

    active BOM 버전 기준.
    - 제품 없음 → 404
    - active BOM 없음 → 404
    - BOM 있으나 items 비어 있음 → 200 + warning 필드
    """
    return await service.get_bom_tree(
        db=db,
        product_id=product_id,
        only_confirmed=only_confirmed,
    )


# ---------------------------------------------------------------------------
# POST /products/bom-versions/{bom_version_id}/activate — BOM 버전 활성화
# ---------------------------------------------------------------------------

@router.post("/bom-versions/{bom_version_id}/activate", status_code=200)
async def activate_bom_version_endpoint(
    bom_version_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    BOM 버전을 active 상태로 전이한다.

    [불변 규칙]
    같은 product의 기존 active 버전은 자동으로 deprecated 전이.
    한 product에 active 버전은 항상 1개만 존재.

    - BOM 버전 없음 → 404
    - 허용되지 않는 전이 (예: deprecated → active) → 422
    """
    return await service.activate_bom_version(
        db=db,
        bom_version_id=bom_version_id,
    )


# ---------------------------------------------------------------------------
# POST /products/bom-versions/{bom_version_id}/deprecate — BOM 버전 deprecated
# ---------------------------------------------------------------------------

@router.post("/bom-versions/{bom_version_id}/deprecate", status_code=200)
async def deprecate_bom_version_endpoint(
    bom_version_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    BOM 버전을 deprecated 상태로 전이한다.

    [주의]
    이 전이 후 해당 product의 active BOM이 없어지면
    GET /products/{id}/bom 이 404를 반환하게 된다.

    - BOM 버전 없음 → 404
    - 허용되지 않는 전이 (예: deprecated → deprecated) → 422
    """
    return await service.deprecate_bom_version(
        db=db,
        bom_version_id=bom_version_id,
    )
