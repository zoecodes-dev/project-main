import os
import uuid
from typing import Any, Dict

from arq.connections import RedisSettings
from sqlalchemy import text

from backend.infrastructure.database import AsyncSessionLocal


async def process_feoc_violation(
    ctx: Dict[str, Any],
    batch_id: uuid.UUID,
    supplier_id: uuid.UUID,
    direct_ownership: float,
    indirect_ownership: float,
    reason: str,
    needs_human_review: bool
) -> str:
    """
    [Verification Queue Worker]
    verify_feoc_rule 서비스가 발행한 큐 작업을 비동기로 받아 처리합니다.
    FEOC 룰 위반 내역을 compliance_results 테이블에 안전하게 기록해요.
    """
    async with AsyncSessionLocal() as db:
        try:
            # IRA 규제 ID 조회 (FEOC 관련)
            reg_query = text("SELECT regulation_id FROM regulations WHERE regulation_code = 'IRA' LIMIT 1")
            reg_result = await db.execute(reg_query)
            reg_id = reg_result.scalar()

            if reg_id:
                insert_query = text("""
                    INSERT INTO compliance_results 
                    (batch_id, regulation_id, supplier_id, verdict, needs_human_review, reasoning_text)
                    VALUES (:batch_id, :reg_id, :supplier_id, 'compliance_violation', :needs_human_review, :reason)
                """)
                await db.execute(insert_query, {
                    "batch_id": batch_id,
                    "reg_id": reg_id,
                    "supplier_id": supplier_id,
                    "needs_human_review": needs_human_review,
                    "reason": reason
                })
                await db.commit()
                
            return f"FEOC 위반 기록 완료 (batch: {batch_id})"
        except Exception as e:
            await db.rollback()
            raise e


class WorkerSettings:
    """ARQ 워커 실행을 위한 설정 클래스입니다."""
    functions = [process_feoc_violation]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    queue_name = "verification_queue"
    max_tries = 3  # 지수 백오프 3회