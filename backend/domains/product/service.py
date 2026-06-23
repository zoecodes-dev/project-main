"""
domains/product/service.py  (담당: 팀원 C)

★ 이 파일은 Product 도메인의 비즈니스 로직을 담당하며, 
   외부 원천(ERP/PLM) 데이터의 Ingest(동기화) 및 연쇄 이벤트 발행 패턴을 구현한다.

레이어 규칙 (PROJECT_CORE 5-1):
  router → service → repository → models  (단방향)
  - service는 비즈니스 로직 + 이벤트 발행만 담당한다. 직접 SQL 실행은 금지(repository 위임).
  - 상태 전이는 반드시 state_machine.py 함수를 경유하여 실행한다.
  - 타 도메인의 코드를 직접 import하지 않는다. 도메인 간 통신은 오직 이벤트(publish)로만 수행한다.

이벤트 발행 규칙 (PROJECT_CORE 5-2):
  - publish(event_name, payload)  ← 2-인자 시그니처 준수. db 세션을 넘기지 않는다.
  - payload는 dataclasses.asdict(이벤트객체)로 만든 뒤 JSON 직렬화 가능하도록 변환(_serialize_payload)한다.
  - ★ 이벤트 발행은 반드시 "DB 커밋(db.commit())이 성공하여 영속화가 확정된 뒤"에 수행한다.

[Product 도메인 주요 결정 사항 반영]
  - 결정 #1 (Ingest): 제품은 본 시스템이 직접 생성하지 않고 외부 원천에서 동기화(UPSERT)한다.
    (BOMImported → LotImported → ProductImported 순서로 연쇄 발행하여 다운스트림의 안정성 보장)
  - 결정 #2 (BOM 트리): N차 공급망의 점진적 발견을 지원하기 위해 get_bom_tree에 only_confirmed 필터 스위치 적용.
  - 최신 규격 반영: events/types.py 업데이트에 따라 external_id, batch_id 파라미터 적용 완료.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.product.repository import ProductRepository
from backend.domains.product.state_machine import (
    activate_bom_version as sm_activate_bom_version,
    deprecate_bom_version as sm_deprecate_bom_version,
)
from backend.events.types import (
    BOMImportedEvent,
    CustomerImportedEvent,
    LotImportedEvent,
    ProductImportedEvent,
)
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
# import_products
# ---------------------------------------------------------------------------

async def import_products(
    db: AsyncSession,
    source_system: str = "SEED",
) -> Dict[str, Any]:
    """
    [결정 #1 + W4] 외부 원천에서 고객사·제품 데이터를 동기화하고 이벤트를 발행한다.

    [W4 확장]
    고객사 ingest가 추가됐어요. fetch_from_source가 고객사 UPSERT를 제품보다
    먼저 처리하고, 이 함수는 그 결과를 받아 이벤트를 올바른 순서로 발행해요.

    [이벤트 발행 순서 — W4]
        1. CustomerImported  — 고객사 UPSERT 완료 (신규 시만)
           ※ 기존 고객사 갱신(is_new=False)은 downstream 변화 없으므로 발행 생략.
        2. BOMImported       — bom_version ingest 완료
        3. LotImported       — batch(Lot) ingest 완료
        4. ProductImported   — 제품 전체 ingest 완료 (마지막)

        CustomerImported를 먼저 발행하는 이유:
          products.customer_id 가 customers.customer_id FK를 참조하기 때문에
          "고객사 준비됨" 신호가 먼저 가야 downstream이 안전하게 처리할 수 있어요.
          ProductImported를 마지막에 발행하는 이유는 기존과 동일 — BOM·Lot 준비 후
          "제품 전체가 준비됐다"는 신호를 보내야 해요.

    [호출 흐름]
        router → service.import_products()
               → repository.fetch_from_source()          # 고객사·제품 UPSERT
               → db.commit()
               → publish("CustomerImported", ...)        # 신규 고객사만
               → 제품별: publish("BOMImported", ...)
                         publish("LotImported", ...)
                         publish("ProductImported", ...)

    [반환]
        동기화된 고객사·제품 수와 목록 요약 dict.
    """
    repo = ProductRepository(db)

    result = await repo.fetch_from_source(source_system=source_system)
    await db.commit()

    customer_results: list = result["customers"]    # [(Customer, is_new), ...]
    products: list         = result["products"]     # [Product, ...]

    # ------------------------------------------------------------------
    # 1. CustomerImported 발행 — 신규 고객사(is_new=True)만
    # is_new=False(기존 갱신)는 downstream에 아무 변화 없으므로 이벤트 생략.
    # ------------------------------------------------------------------
    for customer, is_new in customer_results:
        if not is_new:
            continue

        customer_event = CustomerImportedEvent(
            customer_id=customer.customer_id,
            customer_code=customer.customer_code,
            external_id=customer.external_id,
            is_new=True,
        )
        await publish(
            "CustomerImported",
            _serialize_payload(asdict(customer_event)),
        )

    # ------------------------------------------------------------------
    # 2. 제품별 BOMImported → LotImported → ProductImported
    # ------------------------------------------------------------------
    for product in products:
        # 2-1. BOMImported
        bom_event = BOMImportedEvent(
            product_id=product.product_id,
            bom_version_id=None,  # TODO: W3에서 실제 bom_version_id 조회 후 채움
            external_id=product.external_id,
        )
        await publish(
            "BOMImported",
            _serialize_payload(asdict(bom_event)),
        )

        # 2-2. LotImported
        # ──────────────────────────────────────────────────────
        # [A2 수정 — 은지] batch_id TODO 정리
        #
        # [현재 상태]
        #   A(지혜)의 A1 배치 생성 단일 진입점이 아직 머지되지 않았다.
        #   batch_id=None으로 방어적 동작. LotImported 이벤트에
        #   batch_id가 None이어도 downstream(subscriber)이
        #   None 체크 후 안전하게 처리한다.
        #
        # [A1 머지 후 교체할 코드]
        # ┌────────────────────────────────────────────────────┐
        # │ # A(지혜)의 배치 생성 단일 진입점으로 위임           │
        # │ from backend.handlers.batch_trigger import (       │
        # │     create_batch_for_product,                      │
        # │ )                                                  │
        # │ batch = await create_batch_for_product(            │
        # │     db=db,                                         │
        # │     product_id=product.product_id,                 │
        # │     source_system="MES",                           │
        # │     external_id=product.external_id,               │
        # │ )                                                  │
        # │ actual_batch_id = batch.batch_id                   │
        # └────────────────────────────────────────────────────┘
        #
        # [교체 방법]
        #   1. A1 머지 확인 (handlers/batch_trigger.py 존재 확인)
        #   2. 위 주석의 import + 함수 호출 코드를 해제
        #   3. 아래 batch_id=None 을 batch_id=actual_batch_id 로 교체
        #   4. 이 TODO 주석 블록 삭제
        # ──────────────────────────────────────────────────────
        lot_event = LotImportedEvent(
            batch_id=None,  # → A1 머지 후 actual_batch_id 로 교체
            product_id=product.product_id,
            external_id=product.external_id,
        )
        await publish(
            "LotImported",
            _serialize_payload(asdict(lot_event)),
        )

        # 2-3. ProductImported
        product_event = ProductImportedEvent(
            product_id=product.product_id,
            external_id=product.external_id,
        )
        await publish(
            "ProductImported",
            _serialize_payload(asdict(product_event)),
        )

    return {
        "synced_customer_count": len(customer_results),
        "new_customer_count":    sum(1 for _, is_new in customer_results if is_new),
        "synced_product_count":  len(products),
        "source_system":         source_system,
        "customers": [
            {
                "customer_id":   str(c.customer_id),
                "customer_code": c.customer_code,
                "customer_name": c.customer_name,
                "is_new":        is_new,
                "synced_at":     c.synced_at.isoformat() if c.synced_at else None,
            }
            for c, is_new in customer_results
        ],
        "products": [
            {
                "product_id":   str(p.product_id),
                "product_code": p.product_code,
                "product_name": p.product_name,
                "customer_id":  str(p.customer_id) if p.customer_id else None,
                "model_name":   p.model_name,
                "amperage_ah":  float(p.amperage_ah) if p.amperage_ah else None,
                "source_system": p.source_system,
                "synced_at":    p.synced_at.isoformat() if p.synced_at else None,
            }
            for p in products
        ],
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
    제품 정보 dict. source_system / synced_at 포함 (결정 #1).
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
        "customer_id":     str(product.customer_id) if product.customer_id else None,
        "model_name":      product.model_name,
        "amperage_ah":     float(product.amperage_ah) if product.amperage_ah else None,
        "type":            product.type,
        "specs":           product.specs,
        "source_system":   product.source_system,
        "synced_at":       product.synced_at.isoformat() if product.synced_at else None,
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
    제품 목록을 synced_at 내림차순으로 반환한다.

    [결정 #1] 정렬 기준 changed_at → synced_at (repository와 동일).
    source_system / synced_at 응답에 포함.

    [페이지네이션]
    limit / offset 기반. 기본 20건.
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
            "customer_id":     str(p.customer_id) if p.customer_id else None,
            "model_name":      p.model_name,
            "amperage_ah":     float(p.amperage_ah) if p.amperage_ah else None,
            "source_system":   p.source_system,
            "synced_at":       p.synced_at.isoformat() if p.synced_at else None,
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
    only_confirmed: bool = True,
) -> Dict[str, Any]:
    """
    product_id에 해당하는 제품의 5계층 BOM 트리를 반환한다.

    [파라미터]
    only_confirmed : bool = True
        [결정 #2] supply_chain_map.link_status 필터 스위치.
        True  → confirmed 링크만 포함 (운영 화면 기본값).
        False → pending 포함 전체 트리 (공급망 맵 전체 뷰용).

    [404 분기 — 원인별 상세 메시지]
    ① 제품 자체가 없는 경우  → 404 "제품을 찾을 수 없습니다."
    ② 제품은 있지만 active BOM 버전이 없는 경우
                             → 404 "해당 제품에 active BOM 버전이 존재하지 않습니다."

    repository.get_bom_tree()는 두 경우 모두 None을 반환하므로,
    서비스 계층에서 원인을 직접 구분한다.

    [호출 흐름]
    router → service.get_bom_tree()
               → repository.get_product()          # ① 제품 존재 확인
               → repository.get_bom_tree(only_confirmed)  # ② BOM 트리 반환

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

    # ② BOM 트리 조회 — None이면 active BOM 없음 확정
    result = await repo.get_bom_tree(
        product_id=product_id,
        only_confirmed=only_confirmed,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="해당 제품에 active BOM 버전이 존재하지 않습니다.",
        )

    # bom_items 비어 있는 경우: warning은 그대로 통과 (200 반환)
    # result["tree"] == None + result["warning"] 존재 형태로 반환됨
    return result


# ---------------------------------------------------------------------------
# activate_bom_version
# ---------------------------------------------------------------------------

async def activate_bom_version(
    db: AsyncSession,
    bom_version_id: UUID,
) -> Dict[str, Any]:
    """
    BOM 버전을 active 상태로 전이한다.

    [위임]
    실제 전이 로직은 state_machine.activate_bom_version() 에 위임.
    이 함수는 커밋과 응답 직렬화만 담당.

    [불변 규칙]
    같은 product의 기존 active 버전은 state_machine이 deprecated로 전이.

    [반환]
    전이 완료된 BOM 버전 정보 dict.
    """
    bom = await sm_activate_bom_version(db=db, bom_version_id=bom_version_id)
    await db.commit()

    return {
        "bom_version_id": str(bom.bom_version_id),
        "product_id":     str(bom.product_id),
        "version_number": bom.version_number,
        "status":         bom.status,
        "approved_by":    str(bom.approved_by) if bom.approved_by else None,
        "approved_at":    bom.approved_at.isoformat() if bom.approved_at else None,
    }


# ---------------------------------------------------------------------------
# deprecate_bom_version
# ---------------------------------------------------------------------------

async def deprecate_bom_version(
    db: AsyncSession,
    bom_version_id: UUID,
) -> Dict[str, Any]:
    """
    BOM 버전을 deprecated 상태로 전이한다.

    [위임]
    실제 전이 로직은 state_machine.deprecate_bom_version() 에 위임.

    [주의]
    이 전이 후 해당 product의 active BOM이 없어지면
    get_bom_tree() 가 404를 반환하게 된다.

    [반환]
    전이 완료된 BOM 버전 정보 dict.
    """
    bom = await sm_deprecate_bom_version(db=db, bom_version_id=bom_version_id)
    await db.commit()

    return {
        "bom_version_id": str(bom.bom_version_id),
        "product_id":     str(bom.product_id),
        "version_number": bom.version_number,
        "status":         bom.status,
    }
    
# ---------------------------------------------------------------------------
# list_products_filtered
# ---------------------------------------------------------------------------

async def list_products_filtered(
    db: AsyncSession,
    customer_id: Optional[UUID] = None,
    model_name: Optional[str] = None,
    min_ah: Optional[float] = None,
    max_ah: Optional[float] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    고객사·모델·암페어 범위 필터로 제품 목록을 반환한다.

    repository에서 (Product, customer_name) 튜플로 오는 걸
    여기서 dict로 펼쳐요. customer_name은 조인 결과라서
    Product ORM 객체에 없고 튜플 두 번째 자리에 있어요.
    """
    repo = ProductRepository(db)
    rows = await repo.list_products_filtered(
        customer_id=customer_id,
        model_name=model_name,
        min_ah=min_ah,
        max_ah=max_ah,
        limit=limit,
        offset=offset,
    )

    return [
        {
            "product_id":    str(product.product_id),
            "product_code":  product.product_code,
            "product_name":  product.product_name,
            "customer_id":   str(product.customer_id) if product.customer_id else None,
            "customer_name": customer_name,
            "model_name":    product.model_name,
            "amperage_ah":   float(product.amperage_ah) if product.amperage_ah else None,
            "type":          product.type,
            "source_system": product.source_system,
            "synced_at":     product.synced_at.isoformat() if product.synced_at else None,
        }
        for product, customer_name in rows
    ]


# ---------------------------------------------------------------------------
# get_bom_versions
# ---------------------------------------------------------------------------

async def get_bom_versions(
    db: AsyncSession,
    product_id: UUID,
) -> List[Dict[str, Any]]:
    """
    제품의 전체 BOM 버전 목록을 반환한다 (active  deprecated 포함).

    [404 분기]
    제품 자체가 없으면 404. BOM 버전이 0개인 건 404가 아니라 빈 배열 반환이에요.
    "제품은 있는데 BOM을 아직 안 만든" 상태도 유효하거든요.

    [is_current 필드]
    status='active'인 버전에 is_current=True를 달아줘요.
    프론트가 "현재 버전" 강조 표시를 하기 위한 힌트예요.
    """
    repo = ProductRepository(db)

    product = await repo.get_product(product_id=product_id)
    if product is None:
        raise HTTPException(
            status_code=404,
            detail="제품을 찾을 수 없습니다.",
        )

    versions = await repo.get_bom_versions(product_id=product_id)

    return [
        {
            "bom_version_id":  str(v.bom_version_id),
            "product_id":      str(v.product_id),
            "version_number":  v.version_number,
            "status":          v.status,
            "is_current":      v.status == "active",
            "production_from": v.production_from.isoformat() if v.production_from else None,
            "production_to":   v.production_to.isoformat() if v.production_to else None,
            "approved_by":     str(v.approved_by) if v.approved_by else None,
            "approved_at":     v.approved_at.isoformat() if v.approved_at else None,
            "source_system":   v.source_system,
            "synced_at":       v.synced_at.isoformat() if v.synced_at else None,
        }
        for v in versions
    ]


# ---------------------------------------------------------------------------
# get_bom_version_as_of
# ---------------------------------------------------------------------------

async def get_bom_version_as_of(
    db: AsyncSession,
    product_id: UUID,
    as_of: date,
) -> Dict[str, Any]:
    """
    특정 날짜에 생산 중이었던 BOM 버전을 반환한다.

    [404 분기 — 두 가지]
    ① 제품 없음 → 404 "제품을 찾을 수 없습니다."
    ② 해당 날짜에 맞는 BOM 버전 없음
       → 404 "해당 날짜에 유효한 BOM 버전이 존재하지 않습니다."
    """
    repo = ProductRepository(db)

    product = await repo.get_product(product_id=product_id)
    if product is None:
        raise HTTPException(
            status_code=404,
            detail="제품을 찾을 수 없습니다.",
        )

    version = await repo.get_bom_version_as_of(
        product_id=product_id,
        as_of=as_of,
    )
    if version is None:
        raise HTTPException(
            status_code=404,
            detail=f"해당 날짜({as_of})에 유효한 BOM 버전이 존재하지 않습니다.",
        )

    return {
        "bom_version_id":  str(version.bom_version_id),
        "product_id":      str(version.product_id),
        "version_number":  version.version_number,
        "status":          version.status,
        "is_current":      version.status == "active",
        "production_from": version.production_from.isoformat() if version.production_from else None,
        "production_to":   version.production_to.isoformat() if version.production_to else None,
        "as_of_queried":   as_of.isoformat(),
        "source_system":   version.source_system,
    }    
