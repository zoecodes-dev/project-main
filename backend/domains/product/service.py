# =============================================================================
# backend/domains/product/service.py
#
# KIRA Compliance Intelligence Platform — Product Domain Business Logic Layer
#
# 역할: Product 도메인의 비즈니스 로직 담당.
#   - create_product : 유효성 검사 + DB 저장 + ProductCreated 이벤트 발행
#   - get_product    : 제품 단건 조회 (없으면 404)
#   - list_products  : 제품 목록 조회
#   - get_bom_tree   : BOM 트리 조회
#                      ① 제품 없음 → 404 "제품을 찾을 수 없습니다."
#                      ② active BOM 없음 → 404 "active BOM 버전이 존재하지 않습니다."
#
# 계층 규칙 (PROJECT_CORE.md 5-1):
#   - router.py → service.py → repository.py 단방향 호출.
#   - 타 도메인 코드 직접 import 금지. 통신은 이벤트로만.
#   - 이벤트 발행: publish(event_name, payload) 2-인자 시그니처 준수.
#   - payload: dataclasses.asdict(이벤트객체) 로 생성한 dict.
# =============================================================================

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.product.repository import ProductRepository
from backend.events.types import ProductCreatedEvent
from backend.infrastructure.event_bus import publish


# ---------------------------------------------------------------------------
# _serialize_payload
# ---------------------------------------------------------------------------

def _serialize_payload(payload: dict) -> dict:
    """
    asdict() 결과의 UUID·datetime을 JSON 직렬화 가능한 str로 변환한다.

    publish()의 payload는 JSON 직렬화 가능해야 하므로
    UUID → str, datetime → isoformat() 변환이 필요하다.
    """
    result = {}
    for k, v in payload.items():
        if isinstance(v, uuid.UUID):
            result[k] = str(v)
        elif hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# create_product
# ---------------------------------------------------------------------------

async def create_product(
    db: AsyncSession,
    product_code: str,
    product_name: Optional[str] = None,
    manufacturer_id: Optional[UUID] = None,
    type: Optional[str] = None,
    specs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    제품을 등록하고 ProductCreated 이벤트를 발행한다.

    [비즈니스 규칙]
    - product_code 중복 시 HTTP 409 반환.

    [이벤트 발행]
    - DB 저장 성공 후 ProductCreated 발행.
    - payload: dataclasses.asdict(ProductCreatedEvent) → UUID/datetime str 변환.

    [호출 흐름]
    router → service.create_product() → repository.create_product()
                                      → publish("ProductCreated", payload)

    [반환]
    저장된 제품 정보 dict.
    """
    repo = ProductRepository(db)

    try:
        product = await repo.create_product(
            product_code=product_code,
            product_name=product_name,
            manufacturer_id=manufacturer_id,
            type=type,
            specs=specs,
        )
        await db.commit()

    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"이미 존재하는 product_code입니다: {product_code}",
        )

    # 이벤트 발행 — 2-인자 시그니처 준수 (db 넘기지 않음)
    event = ProductCreatedEvent(product_id=product.product_id)
    await publish(
        "ProductCreated",
        _serialize_payload(asdict(event)),
    )

    return {
        "product_id":      str(product.product_id),
        "product_code":    product.product_code,
        "product_name":    product.product_name,
        "manufacturer_id": str(product.manufacturer_id) if product.manufacturer_id else None,
        "type":            product.type,
        "specs":           product.specs,
        "created_at":      product.created_at.isoformat() if product.created_at else None,
    }


# ---------------------------------------------------------------------------
# get_product
# ---------------------------------------------------------------------------

async def get_product(
    db: AsyncSession,
    product_id: UUID,
) -> Dict[str, Any]:
    """
    product_id로 제품 단건을 조회한다.

    [예외]
    존재하지 않는 product_id → HTTP 404.

    [반환]
    제품 정보 dict.
    """
    repo = ProductRepository(db)
    product = await repo.get_product(product_id=product_id)

    if product is None:
        raise HTTPException(
            status_code=404,
            detail="제품을 찾을 수 없습니다.",
        )

    return {
        "product_id":      str(product.product_id),
        "product_code":    product.product_code,
        "product_name":    product.product_name,
        "manufacturer_id": str(product.manufacturer_id) if product.manufacturer_id else None,
        "type":            product.type,
        "specs":           product.specs,
        "created_at":      product.created_at.isoformat() if product.created_at else None,
        "updated_at":      product.updated_at.isoformat() if product.updated_at else None,
    }


# ---------------------------------------------------------------------------
# list_products
# ---------------------------------------------------------------------------

async def list_products(
    db: AsyncSession,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    제품 목록을 생성일 내림차순으로 반환한다.

    [페이지네이션]
    limit / offset 기반. 기본 20건.

    [반환]
    Product ORM 객체를 JSON 직렬화 가능한 dict 리스트로 변환하여 반환.
    UUID → str, datetime → isoformat() 변환 포함.
    """
    repo = ProductRepository(db)
    products = await repo.list_products(limit=limit, offset=offset)

    return [
        {
            "product_id":      str(p.product_id),
            "product_code":    p.product_code,
            "product_name":    p.product_name,
            "manufacturer_id": str(p.manufacturer_id) if p.manufacturer_id else None,
            "type":            p.type,
            "created_at":      p.created_at.isoformat() if p.created_at else None,
        }
        for p in products
    ]


# ---------------------------------------------------------------------------
# get_bom_tree
# ---------------------------------------------------------------------------

async def get_bom_tree(
    db: AsyncSession,
    product_id: UUID,
) -> Dict[str, Any]:
    """
    product_id에 해당하는 제품의 5계층 BOM 트리를 반환한다.

    [404 분기 — 원인별 상세 메시지]

    repository.get_bom_tree()는 두 경우 모두 None을 반환하므로,
    서비스 계층에서 원인을 직접 구분한다.

    ① 제품 자체가 없는 경우
       get_product()로 먼저 제품 존재 여부를 확인한다.
       → 404 "제품을 찾을 수 없습니다."

    ② 제품은 있지만 active BOM 버전이 없는 경우
       get_bom_tree()가 None을 반환하는 시점에 진입하므로,
       이 분기는 "제품은 존재하나 active BOM 없음"이 확정된 상태.
       → 404 "해당 제품에 active BOM 버전이 존재하지 않습니다."

    [호출 흐름]
    router → service.get_bom_tree()
               → repository.get_product()   # ① 제품 존재 확인
               → repository.get_bom_tree()  # ② active BOM 존재 확인 + 트리 반환

    [반환]
    5계층 중첩 BOM 트리 dict.
    """
    repo = ProductRepository(db)

    # ① 제품 존재 여부 먼저 확인
    product = await repo.get_product(product_id=product_id)
    if product is None:
        raise HTTPException(
            status_code=404,
            detail="제품을 찾을 수 없습니다.",
        )

    # ② 제품은 있으므로 이 시점의 None = active BOM 없음
    result = await repo.get_bom_tree(product_id=product_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="해당 제품에 active BOM 버전이 존재하지 않습니다.",
        )

    return result
