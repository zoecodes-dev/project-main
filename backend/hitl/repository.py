import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

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
