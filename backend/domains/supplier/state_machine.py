import dataclasses
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node
from backend.domains.supplier.models import Supplier
from backend.events.types import SupplierStatusChangedEvent


@trace_node(node_name="verify_supplier_node", node_type="state_machine")
async def verify_supplier(state: Dict[str, Any], db: AsyncSession) -> Dict[str, Any]:
    """
    [상태 전이] 협력사 상태를 'verified'로 변경하고 이벤트를 발행합니다.
    """
    supplier_id = state.get("supplier_id")
    if not supplier_id:
        raise ValueError("State must contain a 'supplier_id'")

    # 1. DB에서 Supplier 조회 (예외 처리 강화)
    stmt = select(Supplier).where(Supplier.supplier_id == supplier_id)
    res = await db.execute(stmt)
    supplier = res.scalar_one_or_none()

    if not supplier:
        raise ValueError(f"Supplier with id {supplier_id} not found.")

    # 2. 상태 변경
    old_status = supplier.status
    new_status = "verified"
    supplier.status = new_status

    # 3. 커밋
    await db.commit()

    # 4. 이벤트 발행 (누락된 부분)
    event = SupplierStatusChangedEvent(
        supplier_id=supplier.supplier_id,
        old_status=old_status,
        new_status=new_status,
        event_name="SupplierStatusChanged"
    )
    await publish(event_name="SupplierStatusChanged", payload=dataclasses.asdict(event))

    return state