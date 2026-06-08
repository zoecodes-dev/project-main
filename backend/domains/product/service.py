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
    [결정 #1] 외부 원천에서 제품 데이터를 동기화하고 이벤트를 발행한다.

    시연 구현:
        원천 = DB 시드 데이터 (source_system='SEED').
        repository.fetch_from_source()가 UPSERT 처리.

    실환경 전환:
        repository._load_seed_products() 내부만 ERP API로 교체.
        이 함수의 로직·이벤트 발행 연계는 변경 불필요.

    [이벤트 발행 순서 — 결정 #1]
        각 제품마다:
          1. BOMImported  — bom_version ingest 완료
          2. LotImported  — batch(Lot) ingest 완료
             ※ W2: batch_id=None 플레이스홀더. W3 batch ingest 구현 시 반드시 처리:
                   - batches 레코드 생성 후 실제 batch_id 주입
                   - batches.source_system = 'MES' (schema DEFAULT와 일치)
                   - batches.external_id, synced_at 세팅 필수
          3. ProductImported — 제품 전체 ingest 완료 (마지막)

        ProductImported를 마지막에 발행하는 이유:
          BOM·Lot 준비가 완료된 시점에 "제품 전체가 준비됐다"는 신호를 보내야
          downstream(SupplyChain, Compliance 등)이 안전하게 처리할 수 있기 때문.

    [호출 흐름]
        router → service.import_products()
               → repository.fetch_from_source()   # UPSERT
               → publish("BOMImported", ...)
               → publish("LotImported", ...)
               → publish("ProductImported", ...)

    [반환]
        동기화된 제품 수와 제품 목록 요약 dict.
    """
    repo = ProductRepository(db)

    products = await repo.fetch_from_source(source_system=source_system)
    await db.commit()

    # 이벤트 발행 — 제품별 순서: BOMImported → LotImported → ProductImported
    for product in products:
        # 1. BOMImported
        bom_event = BOMImportedEvent(
            product_id=product.product_id,
            bom_version_id=None,  # TODO: W3에서 실제 bom_version_id 조회 후 채움
            external_id=product.external_id,
        )
        await publish(
            "BOMImported",
            _serialize_payload(asdict(bom_event)),
        )

        # 2. LotImported
        lot_event = LotImportedEvent(
            batch_id=None,  # TODO(W3): repo.create_batch() 호출 후 실제 batch_id로 교체.   
                            # batches.source_system='MES', external_id, synced_at 세팅 필수.
                            # schema.sql batches 테이블 DEFAULT 'MES' 참조.               
            product_id=product.product_id, 
            external_id=product.external_id,
        )
        await publish(
            "LotImported",
            _serialize_payload(asdict(lot_event)),
        )
        
        # 3. ProductImported
        product_event = ProductImportedEvent(
            product_id=product.product_id,
            external_id=product.external_id,
        )
        await publish(
            "ProductImported",
            _serialize_payload(asdict(product_event)),
        )

    return {
        "synced_count":   len(products),
        "source_system":  source_system,
        "products": [
            {
                "product_id":   str(p.product_id),
                "product_code": p.product_code,
                "product_name": p.product_name,
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
