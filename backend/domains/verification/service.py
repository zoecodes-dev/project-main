import uuid
import dataclasses

from backend.infrastructure.queue import enqueue, VALIDATION_QUEUE
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_tool
from sqlalchemy.ext.asyncio import AsyncSession
from backend.events.types import (
    ValidationStartedEvent,
    ValidationFailedEvent,
    ValidationCompletedEvent
)

@trace_tool("verify_feoc_rule")
async def verify_feoc_rule(db: AsyncSession, batch_id: uuid.UUID, supplier_id: uuid.UUID, direct_ownership: float, indirect_ownership: float = 0.0) -> bool:
    """
    [Verification Engine] FEOC 지분율 규제 심사
    - Decision #4 반영: 직접 지분 25% 이상은 즉시 위반(violation)
    - 간접/합산 지분 25% 이상은 위반 + gray_zone (HITL 사람 확인 필요)
    """
    await publish("ValidationStarted", dataclasses.asdict(ValidationStartedEvent(batch_id=batch_id, rules_applied=["FEOC"], event_name="ValidationStarted")))

    total_ownership = direct_ownership + indirect_ownership
    is_violation = False
    reason = ""
    needs_human_review = False # gray_zone 플래그 역할

    if direct_ownership >= 25.0:
        is_violation = True
        reason = f"FEOC 직접 지분율 25% 이상 위반 (현재: {direct_ownership}%)"
    elif indirect_ownership >= 25.0 or total_ownership >= 25.0:
        is_violation = True
        reason = f"FEOC 간접/합산 지분율 25% 이상 위반 (직접: {direct_ownership}%, 간접: {indirect_ownership}%)"
        needs_human_review = True # Decision #4: 간접 지분 위반 시 HITL 큐로 보내기 위한 표식
        
    if is_violation:
        # 비동기 큐 작업 위임 스펙 준수 (인자 이름 일치)
        await enqueue(
            VALIDATION_QUEUE, 
            "process_feoc_violation", 
            batch_id=batch_id, 
            supplier_id=supplier_id, 
            direct_ownership=direct_ownership, 
            indirect_ownership=indirect_ownership, 
            reason=reason,
            gray_zone=needs_human_review,
            job_id=f"feoc_violation_{batch_id}_{supplier_id}"
        )
        await publish("ValidationFailed", dataclasses.asdict(ValidationFailedEvent(batch_id=batch_id, violated_rules=[reason], event_name="ValidationFailed")))
        return False

    await publish("ValidationCompleted", dataclasses.asdict(ValidationCompletedEvent(batch_id=batch_id, results=[{"rule": "FEOC", "passed": True}], event_name="ValidationCompleted")))
    return True
