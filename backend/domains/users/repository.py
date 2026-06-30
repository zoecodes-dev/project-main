import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.users.models import Tenant, User


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.user_id == user_id))
        return result.scalar_one_or_none()

    async def get_tenant(self, tenant_id: uuid.UUID) -> Optional[Tenant]:
        """테넌트 단건 조회. 구독 상태(§0.2) 확인용."""
        result = await self.db.execute(
            select(Tenant).where(Tenant.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def create_user(
        self,
        email: str,
        password_hash: str,
        role: str,
        supplier_id: Optional[uuid.UUID] = None,
        tenant_id: Optional[uuid.UUID] = None,
        name: Optional[str] = None,
        is_active: bool = True,
    ) -> User:
        """
        계정 신규 생성. 협력사 온보딩 제출 시 supplier service가 동기 호출한다(회원가입).
        커밋하지 않는다(flush만) — 온보딩의 단일 트랜잭션을 호출 service가 한 번에 커밋.
        이메일 UNIQUE 중복은 호출부가 get_by_email 로 선검사해 409로 매핑한다.
        """
        user = User(
            email=email,
            password_hash=password_hash,
            role=role,
            supplier_id=supplier_id,
            tenant_id=tenant_id,
            name=name,
            is_active=is_active,
        )
        self.db.add(user)
        await self.db.flush()
        return user

    async def get_active_by_supplier_id(self, supplier_id: uuid.UUID) -> Optional[User]:
        """supplier 의 활성 계정 단건. 온보딩 재제출 가드(이미 가입된 supplier → 409)용."""
        result = await self.db.execute(
            select(User).where(
                User.supplier_id == supplier_id, User.is_active.is_(True)
            )
        )
        return result.scalars().first()

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
