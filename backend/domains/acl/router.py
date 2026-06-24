"""
domains/acl/router.py  (담당: 팀원 E)

데이터 주권 접근권한 조회 엔드포인트.

[설계 원칙]
  - 실제 ACL 판정 로직은 infrastructure/acl.py 에 두고, 여기는 라우터 역할만 한다.
  - GET /acl/my-suppliers   : 내가 접근 가능한 협력사 ID 목록
  - GET /acl/check/{target} : 특정 협력사에 대한 접근 가능 여부 단건 조회
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db
from backend.infrastructure.acl import (
    check_supplier_access,
    get_accessible_supplier_ids,
    get_supplier_id_for_user,
)

router = APIRouter(prefix="/acl", tags=["ACL"])


class AccessibleSuppliersResponse(BaseModel):
    my_supplier_id: UUID | None
    accessible_supplier_ids: list[UUID]


class AccessCheckResponse(BaseModel):
    my_supplier_id: UUID | None
    target_supplier_id: UUID
    allowed: bool


@router.get("/my-suppliers", response_model=AccessibleSuppliersResponse)
async def get_my_accessible_suppliers(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """현재 로그인 협력사가 조회 가능한 협력사 ID 목록을 반환한다.

    원칙: 자기 자신 + 직상위(parent) + 직하위(children).
    supplier와 매핑되지 않는 user(원청/관리자 등)는 my_supplier_id=None, 목록 빈 배열.
    """
    my_supplier_id = await get_supplier_id_for_user(current_user.user_id, db)
    if my_supplier_id is None:
        return AccessibleSuppliersResponse(my_supplier_id=None, accessible_supplier_ids=[])

    ids = await get_accessible_supplier_ids(my_supplier_id, db)
    return AccessibleSuppliersResponse(my_supplier_id=my_supplier_id, accessible_supplier_ids=ids)


@router.get("/check/{target_supplier_id}", response_model=AccessCheckResponse)
async def check_access_to_supplier(
    target_supplier_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """특정 협력사(target_supplier_id)에 접근 가능한지 단건으로 조회한다."""
    my_supplier_id = await get_supplier_id_for_user(current_user.user_id, db)
    if my_supplier_id is None:
        return AccessCheckResponse(
            my_supplier_id=None,
            target_supplier_id=target_supplier_id,
            allowed=False,
        )

    allowed = await check_supplier_access(my_supplier_id, target_supplier_id, db)
    return AccessCheckResponse(
        my_supplier_id=my_supplier_id,
        target_supplier_id=target_supplier_id,
        allowed=allowed,
    )
