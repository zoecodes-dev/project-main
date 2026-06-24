"""
infrastructure/acl.py  (담당: 팀원 E / 공통)

공급망 데이터 접근권한(ACL) — Wave 3~4 구현.
횡단 관심사(cross-cutting)라 infrastructure 계층에 둔다 (auth.py 와 같은 레이어).

[접근 원칙]
  협력사는 자기 데이터 + 직상위(parent) + 직하위(children)만 접근 가능.
  옆 라인(sibling) 차단 — supply_chain_map 엣지 1-hop 기준으로 판정한다.

[역할별 정책]
  _EXEMPT_ROLES(원청/관리자/감사자): ACL 면제 — 전체 접근.
  협력사: supply_chain_map 직접 연결 노드(self + parent + children)만 허용.

[user → supplier_id 매핑]
  users.tenant_id == suppliers.tenant_id 를 통해 매핑한다.
  tenant당 협력사가 여러 개인 경우 LIMIT 1 (대표 협력사).
"""
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db

# 이 역할들은 공급망 ACL 적용 면제 (전체 데이터 접근 허용)
_EXEMPT_ROLES = {"관리자", "원청", "감사자"}


async def get_supplier_id_for_user(user_id: UUID, db: AsyncSession) -> UUID | None:
    """user_id에 연결된 협력사 supplier_id를 반환한다. 없으면 None.

    users.tenant_id == suppliers.tenant_id 조인으로 매핑.
    """
    row = (await db.execute(
        text("""
            SELECT s.supplier_id
            FROM suppliers s
            JOIN users u ON s.tenant_id = u.tenant_id
            WHERE u.user_id = :uid
            LIMIT 1
        """),
        {"uid": user_id},
    )).one_or_none()
    return row.supplier_id if row else None


async def get_accessible_supplier_ids(
    supplier_id: UUID,
    db: AsyncSession,
) -> list[UUID]:
    """supplier_id가 접근 가능한 협력사 ID 목록을 반환한다.

    허용: 자기 자신 + 직상위(parent) + 직하위(children).
    supply_chain_map 1-hop 엣지만 탐색한다.
    """
    rows = await db.execute(
        text("""
            SELECT DISTINCT id FROM (
                SELECT :sid::uuid AS id
                UNION
                SELECT parent_supplier_id AS id
                  FROM supply_chain_map
                 WHERE child_supplier_id = :sid
                   AND parent_supplier_id IS NOT NULL
                UNION
                SELECT child_supplier_id AS id
                  FROM supply_chain_map
                 WHERE parent_supplier_id = :sid
                   AND child_supplier_id IS NOT NULL
            ) sub
        """),
        {"sid": supplier_id},
    )
    return [row.id for row in rows]


async def check_supplier_access(
    accessor_supplier_id: UUID,
    target_supplier_id: UUID,
    db: AsyncSession,
) -> bool:
    """accessor가 target 협력사 데이터를 읽을 수 있는지 반환한다.

    허용: 자기 자신 + supply_chain_map 직접 연결(parent ↔ child 방향 모두).
    """
    if accessor_supplier_id == target_supplier_id:
        return True

    row = (await db.execute(
        text("""
            SELECT 1
              FROM supply_chain_map
             WHERE (parent_supplier_id = :acc AND child_supplier_id = :tgt)
                OR (child_supplier_id  = :acc AND parent_supplier_id = :tgt)
             LIMIT 1
        """),
        {"acc": accessor_supplier_id, "tgt": target_supplier_id},
    )).one_or_none()
    return row is not None


def require_supplier_self_or_connected(target_supplier_id_param: str = "supplier_id"):
    """라우터 의존성 팩토리 — 협력사 본인 또는 직접 연결 노드만 허용.

    사용법:
        @router.get("/suppliers/{supplier_id}/data",
                    dependencies=[Depends(require_supplier_self_or_connected())])

    _EXEMPT_ROLES는 무조건 통과. 협력사 역할은 check_supplier_access 로 판정.
    """
    async def _checker(
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> CurrentUser:
        if current_user.role in _EXEMPT_ROLES:
            return current_user

        target_id_str = request.path_params.get(target_supplier_id_param)
        if not target_id_str:
            return current_user

        try:
            target_supplier_id = UUID(str(target_id_str))
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"'{target_supplier_id_param}' 형식이 올바르지 않습니다.",
            )

        my_supplier_id = await get_supplier_id_for_user(current_user.user_id, db)
        if my_supplier_id is None:
            return current_user

        allowed = await check_supplier_access(my_supplier_id, target_supplier_id, db)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="해당 협력사 데이터에 접근 권한이 없습니다.",
            )
        return current_user

    return _checker
