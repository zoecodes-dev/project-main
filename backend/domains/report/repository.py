import uuid
from typing import List, Optional

from sqlalchemy import Column, MetaData, String, Table, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.report.models import Report, ReportApprovalStep

# users 테이블 결재선 순회용 경량 매핑.
# users 도메인 ORM을 import하지 않고 필요한 3컬럼만 선언.
_users_lite = Table(
    "users",
    MetaData(),
    Column("user_id", PG_UUID(as_uuid=True)),
    Column("role", String(50)),
    Column("manager_id", PG_UUID(as_uuid=True)),
)


class ReportRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_report(self, report_id: uuid.UUID) -> Optional[Report]:
        result = await self.db.execute(select(Report).where(Report.report_id == report_id))
        return result.scalar_one_or_none()

    async def get_steps(self, report_id: uuid.UUID) -> List[ReportApprovalStep]:
        result = await self.db.execute(
            select(ReportApprovalStep)
            .where(ReportApprovalStep.report_id == report_id)
            .order_by(ReportApprovalStep.step_number)
        )
        return list(result.scalars().all())

    async def get_current_step(self, report_id: uuid.UUID, step_number: int) -> Optional[ReportApprovalStep]:
        result = await self.db.execute(
            select(ReportApprovalStep).where(
                ReportApprovalStep.report_id == report_id,
                ReportApprovalStep.step_number == step_number,
            )
        )
        return result.scalar_one_or_none()

    async def get_inbox(self, approver_id: uuid.UUID) -> List[ReportApprovalStep]:
        """결재자 대기함 — 본인에게 할당된 pending 단계 목록."""
        result = await self.db.execute(
            select(ReportApprovalStep).where(
                ReportApprovalStep.approver_id == approver_id,
                ReportApprovalStep.status == "pending",
            )
        )
        return list(result.scalars().all())

    async def get_manager_chain(self, start_user_id: uuid.UUID) -> List[dict]:
        """
        manager_id 자기참조 체인을 NULL 에 도달할 때까지 순회.
        반환: [{"user_id": UUID, "role": str}, ...] 직속→최상위 순서.
        visited 체크로 순환 참조 방지.
        """
        chain: List[dict] = []
        visited: set[uuid.UUID] = {start_user_id}
        current_id = start_user_id

        while True:
            row = await self.db.execute(
                select(
                    _users_lite.c.user_id,
                    _users_lite.c.role,
                    _users_lite.c.manager_id,
                ).where(_users_lite.c.user_id == current_id)
            )
            current = row.mappings().one_or_none()
            if not current or not current["manager_id"]:
                break

            manager_id = current["manager_id"]
            if manager_id in visited:
                break

            mgr_row = await self.db.execute(
                select(
                    _users_lite.c.user_id,
                    _users_lite.c.role,
                    _users_lite.c.manager_id,
                ).where(_users_lite.c.user_id == manager_id)
            )
            mgr = mgr_row.mappings().one_or_none()
            if not mgr:
                break

            visited.add(manager_id)
            chain.append({"user_id": mgr["user_id"], "role": mgr["role"]})
            current_id = manager_id

        return chain

    async def add_report(self, report: Report) -> Report:
        self.db.add(report)
        await self.db.flush()
        return report

    async def add_steps(self, steps: List[ReportApprovalStep]) -> None:
        for step in steps:
            self.db.add(step)
        await self.db.flush()
