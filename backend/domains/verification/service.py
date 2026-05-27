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
async def verify_feoc_rule(db: AsyncSession, batch_id: uuid.UUID, supplier_id: uuid.UUID, ownership_percent: float) -> bool:
    """
    [Verification Engine] FEOC 지분율 규제 심사
    - 지분율 25% 이상 시 위반 판정으로 ValidationFailedEvent 발행 및 VALIDATION_QUEUE 적재.
    """
    await publish("ValidationStarted", dataclasses.asdict(ValidationStartedEvent(batch_id=batch_id, rules_applied=["FEOC"], event_name="ValidationStarted")))

    if ownership_percent >= 25.0:
        reason = f"FEOC ownership is 25% or more (Current: {ownership_percent}%)"
        
        # 비동기 큐 작업 위임 스펙 준수 (인자 이름 일치)
        await enqueue(
            VALIDATION_QUEUE, 
            "process_feoc_violation", 
            batch_id=batch_id, 
            supplier_id=supplier_id, 
            ownership_percent=ownership_percent, 
            reason=reason,
            job_id=f"feoc_violation_{batch_id}_{supplier_id}"
        )
        await publish("ValidationFailed", dataclasses.asdict(ValidationFailedEvent(batch_id=batch_id, violated_rules=[reason], event_name="ValidationFailed")))
        return False

    await publish("ValidationCompleted", dataclasses.asdict(ValidationCompletedEvent(batch_id=batch_id, results=[{"rule": "FEOC", "passed": True}], event_name="ValidationCompleted")))
    return True
