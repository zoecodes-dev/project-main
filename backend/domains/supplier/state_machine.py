from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.infrastructure.trace import trace_node
from backend.domains.supplier.models import Supplier

@trace_node(node_name="verify_supplier_node", node_type="state_machine")
async def verify_supplier(state: Dict[str, Any], db: Any) -> Dict[str, Any]:
    # 1. DB에서 Supplier 조회
    stmt = select(Supplier).where(Supplier.supplier_id == state["supplier_id"])
    res = await db.execute(stmt)
    supplier = res.scalar_one()

    # 2. 상태 변경
    supplier.status = "verified"
    
    # 3. 중요: 커밋 누락 시 상태는 바뀌지 않습니다!
    await db.commit() 
    
    return state