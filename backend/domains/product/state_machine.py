# =============================================================================
# backend/domains/product/state_machine.py
#
# KIRA Compliance Intelligence Platform — Product Domain State Machine
#
# 역할: BOM 버전 상태 전이를 담당하는 전용 레이어.
#   - activate_bom_version   : draft/deprecated → active 전이
#   - deprecate_bom_version  : draft/active → deprecated 전이
#
# [왜 이 파일이 필요한가]
#   schema.sql bom_versions 주석:
#     "status 전이는 반드시 domains/product/state_machine.py 를 통해서만."
#   service.py / router.py 에서 bom.status = "active" 직접 대입 금지.
#   모든 상태 전이는 이 파일의 함수를 거쳐야 한다.
#
# [불변 규칙 — PROJECT_CORE.md 3-1]
#   - 한 product에 active BOM 버전은 1개만 존재.
#   - activate_bom_version() 호출 시 기존 active를 먼저 deprecated로 전이.
#   - deprecated → active 역전이 금지 (터미널에 가까운 상태).
#
# [데코레이터 규칙 — PROJECT_CORE.md 5-5]
#   - 상태 변경 함수 → @trace_node (audit_trail 자동 기록 대상)
#   - node_type="agent" : 도메인 비즈니스 로직 수행 노드
#
# [계층 규칙]
#   - service.py → state_machine.py → repository.py (직접 DB 접근)
#   - router.py 에서 직접 호출 금지.
#   - 이벤트 발행 없음 (이벤트는 service.py 책임).
#   - 커밋 없음 (커밋은 service.py 책임).
# =============================================================================

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.product.models import (
    BomVersion,
    BomVersionStatus,
    BOM_STATUS_TRANSITIONS,
)
from backend.infrastructure.trace import trace_node


# ---------------------------------------------------------------------------
# _validate_transition
# ---------------------------------------------------------------------------

def _validate_transition(current_status: str, target_status: str) -> None:
    """
    BOM 버전 상태 전이 허용 여부를 검증한다.

    BOM_STATUS_TRANSITIONS 매트릭스 기준:
      draft      → active, deprecated  (허용)
      active     → deprecated           (허용)
      deprecated → (없음)               (터미널 상태, 모든 전이 거부)

    [예외]
    허용되지 않는 전이 → HTTP 422 반환.
    """
    allowed = BOM_STATUS_TRANSITIONS.get(current_status, frozenset())
    if target_status not in allowed:
        raise HTTPException(
            status_code=422,
            detail=(
                f"허용되지 않는 BOM 버전 상태 전이: "
                f"{current_status!r} → {target_status!r}. "
                f"허용 전이: {sorted(allowed) or '없음 (터미널 상태)'}"
            ),
        )


# ---------------------------------------------------------------------------
# activate_bom_version
# ---------------------------------------------------------------------------

@trace_node(node_name="activate_bom_version", node_type="agent")
async def activate_bom_version(
    db: AsyncSession,
    bom_version_id: UUID,
) -> BomVersion:
    """
    BOM 버전을 active 상태로 전이한다.

    [불변 규칙]
    한 product에 active 버전은 1개만 존재해야 한다.
    따라서 아래 순서를 반드시 지킨다:
      1. 대상 BOM 버전 조회 및 존재 확인.
      2. 전이 허용 여부 검증 (BOM_STATUS_TRANSITIONS 매트릭스).
      3. 같은 product의 기존 active 버전을 deprecated로 전이.
      4. 대상 BOM 버전을 active로 전이.

    3번을 4번보다 먼저 실행해야 active 중복이 생기지 않는다.

    [커밋]
    커밋은 호출한 service.py 에서 담당. 이 함수는 flush()까지만 수행.

    [반환]
    전이 완료된 BomVersion ORM 객체.
    """
    # 1. 대상 BOM 버전 조회
    bom = await db.get(BomVersion, bom_version_id)
    if bom is None:
        raise HTTPException(
            status_code=404,
            detail=f"BOM 버전을 찾을 수 없습니다: {bom_version_id}",
        )

    # 2. 전이 허용 여부 검증
    _validate_transition(
        current_status=bom.status,
        target_status=BomVersionStatus.ACTIVE.value,
    )

    # 3. 같은 product의 기존 active 버전 → deprecated 전이
    #    (active 중복 방지 — PROJECT_CORE.md 3-1 불변 규칙)
    await db.execute(
        update(BomVersion)
        .where(BomVersion.product_id == bom.product_id)
        .where(BomVersion.status == BomVersionStatus.ACTIVE.value)
        .where(BomVersion.bom_version_id != bom_version_id)  # 대상 자신 제외
        .values(status=BomVersionStatus.DEPRECATED.value)
    )

    # 4. 대상 BOM 버전 → active 전이
    bom.status = BomVersionStatus.ACTIVE.value
    await db.flush()
    await db.refresh(bom)

    return bom


# ---------------------------------------------------------------------------
# deprecate_bom_version
# ---------------------------------------------------------------------------

@trace_node(node_name="deprecate_bom_version", node_type="agent")
async def deprecate_bom_version(
    db: AsyncSession,
    bom_version_id: UUID,
) -> BomVersion:
    """
    BOM 버전을 deprecated 상태로 전이한다.

    [주의]
    deprecated는 터미널에 가까운 상태다.
    이 전이 후 active 버전이 없어지면 해당 product의 BOM 트리 조회가
    404를 반환하게 된다. 호출 전 product의 BOM 운영 상태를 확인할 것.

    [커밋]
    커밋은 호출한 service.py 에서 담당.

    [반환]
    전이 완료된 BomVersion ORM 객체.
    """
    # 1. 대상 BOM 버전 조회
    bom = await db.get(BomVersion, bom_version_id)
    if bom is None:
        raise HTTPException(
            status_code=404,
            detail=f"BOM 버전을 찾을 수 없습니다: {bom_version_id}",
        )

    # 2. 전이 허용 여부 검증
    _validate_transition(
        current_status=bom.status,
        target_status=BomVersionStatus.DEPRECATED.value,
    )

    # 3. deprecated 전이
    bom.status = BomVersionStatus.DEPRECATED.value
    await db.flush()
    await db.refresh(bom)

    return bom
