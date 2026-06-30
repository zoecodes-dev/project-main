import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db
from backend.infrastructure.security import create_access_token, verify_password
from backend.domains.users.repository import UserRepository
from backend.domains.users.service import UserService

router = APIRouter(tags=["Users"])


class LoginRequest(BaseModel):
    email: str
    password: str


def _get_service(db: AsyncSession = Depends(get_db)) -> UserService:
    return UserService(UserRepository(db))


def _supplier_id_for(user) -> str | None:
    """
    협력사 계정의 본인 supplier_id (§0.5). users.supplier_id 매핑을 사용한다.
    OEM 계정(admin·owner_*)은 매핑이 없어 None.
    """
    sid = getattr(user, "supplier_id", None)
    return str(sid) if sid else None


@router.post("/auth/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    §1.1 — 이메일/비밀번호 → JWT 발급.
    토큰에 tenant_id/supplier_id 클레임을 심어 이후 요청의 테넌트 격리(§0.2)에 쓴다.
    비활성 계정·비활성 테넌트(subscription_status != active)는 403.
    응답은 snake_case (lib/api.ts snakeToCamel 가 camelCase 로 변환).
    """
    repo = UserRepository(db)
    user = await repo.get_by_email(body.email)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다.")

    # 테넌트 구독 상태 확인 (§0.2): 비활성 테넌트는 로그인 차단.
    if user.tenant_id is not None:
        tenant = await repo.get_tenant(user.tenant_id)
        if tenant is not None and tenant.subscription_status != "active":
            raise HTTPException(
                status_code=403,
                detail="구독이 비활성 상태인 테넌트입니다. 관리자에게 문의하세요.",
            )

    supplier_id = _supplier_id_for(user)
    token = create_access_token(
        {
            "sub": str(user.user_id),
            "role": user.role,
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "supplier_id": supplier_id,
        }
    )
    return {
        "token": token,
        "role": user.role,
        "user_id": str(user.user_id),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "supplier_id": supplier_id,
        "display_name": user.name,
        # 회원가입 게이팅 명시(전방호환). Phase1: 계정은 온보딩 제출 시에만 생기므로
        # '계정 존재 ⇒ 온보딩 완료'. (Phase2에서 미완료 단계 도입 시 여기서 분기)
        "onboarding_complete": True,
    }


@router.get("/auth/me")
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """§1.3 — 현재 토큰의 사용자/역할/테넌트 조회. 토큰 신원 + DB 의 displayName/email."""
    repo = UserRepository(db)
    user = await repo.get_by_id(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    return {
        "user_id": str(user.user_id),
        "role": user.role,
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "supplier_id": str(current_user.supplier_id) if current_user.supplier_id else None,
        "display_name": user.name,
        "email": user.email,
        # 회원가입 게이팅 명시(전방호환). Phase1: 계정 존재 ⇒ 온보딩 완료.
        "onboarding_complete": True,
    }


@router.post("/auth/logout", status_code=204)
async def logout(_: CurrentUser = Depends(get_current_user)):
    """
    §1.2(선택) — 서버측 토큰 블랙리스트 미도입(무상태 JWT).
    실제 만료는 프론트가 localStorage 토큰 삭제로 처리. 엔드포인트 존재만 보장(204).
    """
    return None


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
