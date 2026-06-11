import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db
from backend.domains.users.repository import UserRepository
from backend.domains.users.service import UserService

router = APIRouter(tags=["Users"])


def _get_service(db: AsyncSession = Depends(get_db)) -> UserService:
    return UserService(UserRepository(db))


@router.get("/users/{user_id}")
async def get_user(
    user_id: uuid.UUID,
    service: UserService = Depends(_get_service),
    _: CurrentUser = Depends(get_current_user),
):
    try:
        user = await service.get_user(user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "user_id": user.user_id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "manager_id": user.manager_id,
        "tenant_id": user.tenant_id,
    }


@router.get("/approval-chain")
async def get_approval_chain(
    user_id: uuid.UUID,
    service: UserService = Depends(_get_service),
    _: CurrentUser = Depends(get_current_user),
):
    """
    user_id 의 manager_id 체인을 따라 결재선 반환.
    결재선은 조직도로만 결정. [직속상관, 그 위, ...] 순서.
    """
    chain = await service.get_approval_chain(user_id)
    return [
        {"step": i + 1, "user_id": u.user_id, "name": u.name, "role": u.role}
        for i, u in enumerate(chain)
    ]
