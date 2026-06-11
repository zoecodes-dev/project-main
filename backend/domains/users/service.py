import uuid
from typing import List

from backend.domains.users.models import User
from backend.domains.users.repository import UserRepository


class UserService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self.repo.get_by_id(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")
        return user

    async def get_approval_chain(self, user_id: uuid.UUID) -> List[User]:
        """
        user_id 의 manager_id 체인을 끝까지 순회해 결재자 목록 반환.
        결재선은 조직도(manager_id)로만 결정. 위반유형·심각도 무관.
        """
        return await self.repo.get_manager_chain(user_id)
