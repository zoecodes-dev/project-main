"""
infrastructure/auth.py  (담당: 팀원 B / 공통)

공통 인증·인가 의존성. 도메인이 아니라 횡단 관심사(cross-cutting)라
infrastructure 계층에 둔다. 모든 라우터가 여기서 import 해서 공유한다.

[설계 원칙 — 순환 참조 차단]
  CurrentUser 는 ORM/도메인 모델이 아니라 순수 DTO(pydantic)다.
  토큰 payload 의 user_id·role 만 꺼내 즉시 반환하며, DB 를 조회해
  audit 등 도메인 모델을 import 하지 않는다. (infra → domain import 금지)

[토큰 payload 계약 — create_access_token 과 1:1]
  로그인 시 create_access_token({"sub": str(user_id), "role": role}) 형태로
  발급한다고 가정한다. 여기서는 그 키(sub / role)만 읽는다.
  (현재 코드에 로그인 엔드포인트는 없으나, security.py 의 create_access_token /
   verify_access_token 시그니처는 그대로 쓰고 키 규약만 여기서 고정한다.)

[검증 방식]
  Authorization: Bearer <token> 헤더 → verify_access_token(token) → payload.
  토큰 없음/무효/필수 클레임 누락 → 401.
"""
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from backend.infrastructure.security import verify_access_token

# auto_error=False: 토큰이 없을 때 FastAPI 가 곧장 403 을 던지지 않게 하고,
# 우리가 401 + 명확한 메시지로 통일해서 응답하도록 직접 처리한다.
_bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    """JWT payload 에서 꺼낸 최소 신원 정보 DTO. (DB 미조회, 순환참조 차단)"""
    user_id: UUID
    role: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    """
    공용 인증 의존성.
    Authorization: Bearer <token> → verify_access_token → CurrentUser(user_id, role).
    토큰이 없거나 무효거나 필수 클레임(sub/role)이 빠지면 401.

    각 도메인 라우터는 엔드포인트에 Depends(get_current_user) 만 붙이면 된다.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorized("인증 토큰이 필요합니다.")

    payload = verify_access_token(credentials.credentials)
    if payload is None:
        raise _unauthorized("토큰이 무효하거나 만료되었습니다.")

    sub = payload.get("sub")
    role = payload.get("role")
    if sub is None or role is None:
        raise _unauthorized("토큰에 user_id/role 클레임이 없습니다.")

    try:
        user_id = UUID(str(sub))
    except (ValueError, TypeError):
        raise _unauthorized("토큰의 user_id 형식이 올바르지 않습니다.")

    return CurrentUser(user_id=user_id, role=str(role))


def require_role(*roles: str):
    """
    역할(role) 검사 의존성 팩토리.
    예) 원청 전용 엔드포인트 보호:
        @router.get(..., dependencies=[Depends(require_role("원청"))])
        또는 current_user 까지 받고 싶으면:
        current_user: CurrentUser = Depends(require_role("원청", "감사자"))

    허용 role 목록에 없으면 403.
    """
    async def checker(
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"이 작업은 다음 역할만 가능합니다: {', '.join(roles)}",
            )
        return current_user

    return checker