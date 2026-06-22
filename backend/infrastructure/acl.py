"""
infrastructure/acl.py  (담당: 팀원 E / 공통)

공급망 데이터 접근권한(ACL) 인터페이스 — Wave 0 스텁.
횡단 관심사(cross-cutting)라 infrastructure 계층에 둔다 (auth.py 와 같은 레이어).

[접근 원칙 — Wave 3~4 구현 시 채울 것]
  협력사는 자기 데이터 + 직상위(parent) + 직하위(children)만 접근 가능.
  옆 라인 차단 — supply_chain_map 엣지를 기준으로 판정한다.

[스텁 규약]
  현재 check_supplier_access → True, get_accessible_supplier_ids → [own_id] 반환.
  A1·B1·C1 등 의존 작업은 이 시그니처에 대고 코딩한다.
  실 구현은 수요일(ACL Wave 3~4)에 채운다.
"""
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db


async def check_supplier_access(
    accessor_supplier_id: UUID,
    target_supplier_id: UUID,
    db: AsyncSession,
) -> bool:
    """accessor가 target 협력사 데이터를 읽을 수 있는지 반환한다.

    허용: 자기 자신 + 직상위(parent_supplier_id) + 직하위(child in supply_chain_map).
    TODO(Wave 3): supply_chain_map 조회로 실 구현.
    """
    # 스텁: 자기 자신이면 허용, 나머지는 향후 구현 전까지 허용
    return True


async def get_accessible_supplier_ids(
    supplier_id: UUID,
    db: AsyncSession,
) -> list[UUID]:
    """supplier_id가 접근 가능한 협력사 ID 목록을 반환한다.

    TODO(Wave 3): supply_chain_map 직접 연결 노드 조회로 실 구현.
    """
    # 스텁: 자기 자신만 반환
    return [supplier_id]


def require_supplier_self_or_connected(target_supplier_id_param: str = "supplier_id"):
    """라우터 의존성 팩토리 — 협력사 본인 또는 직접 연결 노드만 허용.

    사용법:
        @router.get("/suppliers/{supplier_id}/data",
                    dependencies=[Depends(require_supplier_self_or_connected())])

    TODO(Wave 3): current_user → supplier_id 매핑 + check_supplier_access 연동.
    """
    async def _checker(
        current_user: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> CurrentUser:
        # 스텁: 인증된 사용자면 통과 (Wave 3에서 실 ACL 로직으로 교체)
        return current_user

    return _checker
