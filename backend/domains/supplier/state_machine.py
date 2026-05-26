"""
domains/supplier/state_machine.py  (담당: 팀원 B)

협력사 status 전이 엔진. submission 도메인의 SUBMISSION_TRANSITIONS와 같은 패턴.
- 모든 status 변경은 transition_supplier_status()를 통한다(직접 대입 금지).
- 전이 매트릭스로 "현재 → 갈 수 있는 상태"를 검증한다(허용값 존재 여부만이 아니라).

★ 아래 SUPPLIER_TRANSITIONS의 전이 흐름은 제안값이다. 실제 업무 흐름에 맞는지
   B가 검토해서 확정할 것. (schema.sql suppliers.status 허용값 7종 기준)
"""
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.trace import trace_node
from backend.domains.supplier.models import Supplier


# ── 상태 전이 매트릭스 (★흐름은 검토 후 확정) ──────────────────
# schema.sql suppliers.status 허용값: pending / requested / in_progress /
#   review / verified / violation / suspended (전부 언더스코어)
SUPPLIER_TRANSITIONS: Dict[str, list] = {
    "pending":     ["requested"],                       # 등록됨 → 자료요청 발송
    "requested":   ["in_progress"],                     # 요청됨 → 협력사 입력중
    "in_progress": ["review"],                          # 입력완료 → 검토대기
    "review":      ["verified", "violation"],           # 검토 → 통과 또는 위반
    "verified":    ["violation", "suspended"],          # 검증됨 → 사후 위반/정지 가능
    "violation":   ["review", "suspended"],             # 위반 → 재검토 또는 정지
    "suspended":   ["review"],                          # 정지 → 재검토로 복귀
}


@trace_node(node_name="transition_supplier_status", node_type="system")
async def transition_supplier_status(
    db: AsyncSession,
    supplier: Supplier,
    new_status: str,
    batch_id: str = None,
) -> Supplier:
    """
    협력사 status를 전이 매트릭스에 따라 변경한다.
    - 현재 status에서 new_status로 가는 전이가 허용되지 않으면 ValueError.
    - flush까지만(커밋은 호출자/service). batch_id가 있으면 @trace_node가 감사 기록.
    """
    current = supplier.status
    allowed = SUPPLIER_TRANSITIONS.get(current, [])

    if new_status not in allowed:
        raise ValueError(
            f"허용되지 않는 상태 전이: {current} → {new_status} "
            f"(가능: {allowed})"
        )

    supplier.status = new_status
    db.add(supplier)
    
    await db.flush()
    return supplier


@trace_node(node_name="verify_supplier_node", node_type="state_machine")
async def verify_supplier(state: Dict[str, Any], db: AsyncSession) -> Dict[str, Any]:
    """
    협력사를 'verified'로 전이한다.
    ★ 직접 대입하지 않고 transition_supplier_status()를 거쳐 매트릭스 검증을 받는다.
      (review 상태에서만 verified로 갈 수 있다 — 위 매트릭스 참고)
    커밋은 service가 일원화하지만, 이 함수는 파이프라인 노드로도 쓰일 수 있어
    상태 확정을 위해 commit한다. (호출 맥락에 맞게 B가 조정)
    """
    stmt = select(Supplier).where(Supplier.supplier_id == state["supplier_id"])
    res = await db.execute(stmt)
    supplier = res.scalar_one()

    await transition_supplier_status(
        db, supplier, "verified", batch_id=state.get("batch_id")
    )
    await db.commit()

    return state