from sqlalchemy.ext.asyncio import AsyncSession
from backend.infrastructure.trace import trace_node

@trace_node(node_name="verify_supplier_node", node_type="state_machine")
async def verify_supplier(state: dict, db: AsyncSession) -> dict:
    """
    공급업체의 진위 여부 및 제재 등급에 따른 물리적 수용 제어를 전담할 상태 머신 (골격)
    TODO: W2 - PENDING -> VERIFIED 전이 로직 및 SupplierStatusChanged 이벤트 발행 구현 예정
    """
    # 1주차는 인프라 파이프라인 우회 및 에러 방지를 위해 입력받은 state를 그대로 반환하는 깡통 상태 유지
    return state