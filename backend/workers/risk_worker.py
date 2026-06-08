import os
import uuid
import dataclasses
from typing import Any, Dict, List

from arq.connections import RedisSettings

from backend.infrastructure.database import AsyncSessionLocal
from backend.infrastructure.event_bus import publish
from backend.events.types import HITLRequestedEvent
from backend.domains.audit.state_machine import pause_batch_for_review
from backend.domains.audit.repository import create_pending_hitl_review
from backend.domains.risk.service import calculate_risk_score


async def process_risk_event(
    ctx: Dict[str, Any],
    batch_id: uuid.UUID,
    supplier_id: uuid.UUID,
    violations: List[Dict[str, Any]]
) -> str:
    """
    [Risk Queue Worker]
    다른 에이전트들이 큐에 던진 위반 사항(violations)을 비동기로 받아서
    Risk 도메인의 가점식 점수를 계산하고 에스컬레이션 여부를 판정해요.
    """
    async with AsyncSessionLocal() as db:
        try:
            result = await calculate_risk_score(
                db=db, 
                batch_id=batch_id, 
                supplier_id=supplier_id, 
                violations=violations
            )

            # 점수 계산 결과, 에스컬레이션이 필요하면 HITL 상태로 전환해요.
            if result.get("is_escalated"):
                await pause_batch_for_review(db, batch_id)
                review_id, created = await create_pending_hitl_review(
                    db,
                    batch_id=batch_id,
                    reason="risk_escalated",
                    trigger_stage="stage_risk_worker"  # 워커에서 트리거되었음을 명시
                )
                await db.commit()

                # HITL 요청 이벤트를 발행해서 다른 시스템에 알려줘요.
                if created:
                    event = HITLRequestedEvent(batch_id=batch_id, reason="risk_escalated")
                    await publish(event.event_name, dataclasses.asdict(event))
                
                return f"리스크 에스컬레이션 처리 완료 (배치: {batch_id})"

            # 에스컬레이션이 아니면, 서비스 함수 내에서 변경된 사항만 커밋해요.
            await db.commit()
            return f"리스크 계산 완료 (배치: {batch_id}, 점수: {result.get('overall_risk_score', 0)})"
        except Exception as e:
            await db.rollback()
            raise e

class WorkerSettings:
    """ARQ 워커 실행을 위한 설정 클래스입니다."""
    functions = [process_risk_event]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    queue_name = "risk_queue"
    max_tries = 3  # 지수 백오프 3회 후 데드레터 큐(DLQ)로 이동해요