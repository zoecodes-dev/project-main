import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from backend.hitl.models import HitlReview

class HitlRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_queue_by_status(self, status: str):
        stmt = select(HitlReview).where(HitlReview.status == status)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_by_batch_id(self, batch_id: uuid.UUID) -> HitlReview | None:
        stmt = select(HitlReview).where(HitlReview.batch_id == batch_id)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def get_queue_with_batch_info(self, status: str) -> list[dict]:
        """
        [도메인 격리 준수] batches 테이블을 조인하여 에이전트 신뢰도(confidence_score)를 가져옵니다.
        """
        stmt = text("""
            SELECT 
                h.review_id, h.batch_id, h.reason, h.trigger_stage, h.status, h.created_at,
                b.confidence_score
            FROM hitl_reviews h
            JOIN batches b ON h.batch_id = b.batch_id
            WHERE h.status = :status
            ORDER BY h.created_at ASC
        """)
        result = await self.db.execute(stmt, {"status": status})
        return [dict(row._mapping) for row in result.fetchall()]
