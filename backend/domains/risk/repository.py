from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.risk.models import RiskProfile


class RiskRepository:
    """
    리스크 프로필 데이터에 접근하는 Repository 계층입니다.
    비즈니스 로직(점수 계산, 상태 전이 등)은 service.py로 위임하고 순수 DB 작업만 담당합니다.
    """
    @staticmethod
    async def list_profiles(db: AsyncSession, level: str | None = None) -> list[RiskProfile]:
        """
        리스크 프로필 목록을 다건 조회합니다. (프론트엔드 목록 렌더링용)
        """
        stmt = select(RiskProfile)
        if level:
            stmt = stmt.where(RiskProfile.risk_level == level)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_supplier_id(db: AsyncSession, supplier_id: UUID) -> RiskProfile | None:
        """
        특정 협력사의 리스크 프로필을 조회합니다.
        """
        result = await db.execute(select(RiskProfile).where(RiskProfile.supplier_id == supplier_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def create(db: AsyncSession, profile: RiskProfile) -> RiskProfile:
        """
        새로운 리스크 프로필을 데이터베이스에 저장합니다.
        - 비동기 세션을 사용하므로 add 후 flush()를 호출하여 DB에 반영합니다.
        - 기존 프로필 업데이트 시에는 모델 객체를 수정하면 ORM이 자동으로 추적하여 commit 시점에 반영합니다.
        """
        db.add(profile)
        await db.flush()
        return profile