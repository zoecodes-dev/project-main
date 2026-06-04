import os
import uuid
from typing import Any, Dict, List

from arq.connections import RedisSettings

from backend.infrastructure.database import AsyncSessionLocal
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
            return f"리스크 계산 완료 (배치: {batch_id}, 점수: {result.get('overall_risk_score')})"
        except Exception as e:
            await db.rollback()
            raise e

class WorkerSettings:
    """ARQ 워커 실행을 위한 설정 클래스입니다."""
    functions = [process_risk_event]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    queue_name = "risk_queue"
    max_tries = 3  # 지수 백오프 3회 후 데드레터 큐(DLQ)로 이동해요