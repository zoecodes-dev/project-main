from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.trace import trace_node


@trace_node(node_name="create_audit_entry", node_type="human")
async def create_audit_entry(
    db: AsyncSession,
    batch_id: UUID,
    decision_text: str,
) -> dict:
    """
    human 노드 결정 기록용 깡통 함수.
    실제 INSERT는 @trace_node 데코레이터가 처리.
    decision_text / citations 직접 저장은 W2.
    """
    # TODO W2: decision_text / citations 컬럼 직접 UPDATE 로직 추가
    return {
        "batch_id": str(batch_id),
        "decision_text": decision_text,
        "status": "recorded",
    }
