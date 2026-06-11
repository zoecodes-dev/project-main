import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.users.models import User


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.user_id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_manager_chain(self, user_id: uuid.UUID) -> List[User]:
        """
        user_id 에서 manager_id 를 따라 NULL 에 도달할 때까지 순회.
        반환: [직속상관, 그 위, ...] 순서. visited 체크로 순환 참조 방지.
        """
        chain: List[User] = []
        visited: set[uuid.UUID] = {user_id}
        current_id: uuid.UUID = user_id

        while True:
            result = await self.db.execute(
                select(User.manager_id).where(User.user_id == current_id)
            )
            manager_id = result.scalar_one_or_none()

            if manager_id is None or manager_id in visited:
                break

            result = await self.db.execute(
                select(User).where(User.user_id == manager_id)
            )
            manager = result.scalar_one_or_none()
            if manager is None:
                break

            visited.add(manager_id)
            chain.append(manager)
            current_id = manager_id

        return chain
