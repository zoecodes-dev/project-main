import uuid
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.report.models import Report, ReportApprovalStep
from backend.domains.report.repository import ReportRepository
from backend.domains.report.state_machine import ReportStateMachine
from backend.infrastructure.event_bus import publish

class ReportService:
    def __init__(self, repo: ReportRepository):
        self.repo = repo

    async def create_report(
        self,
        *,
        requester_id: uuid.UUID,
        title: str,
        description: str | None,
        batch_id: uuid.UUID | None,
    ) -> Report:
        """
        보고서 초안 생성 + 결재선 구성.
        결재선은 기안자의 manager_id 체인 전체. 위반유형·심각도 무관.
        """
        chain = await self.repo.get_manager_chain(requester_id)
        if not chain:
            raise ValueError("결재선을 구성할 수 없습니다. manager_id 를 확인하세요.")

        report = Report(
            requester_id=requester_id,
            title=title,
            description=description,
            batch_id=batch_id,
            status="draft",
            current_step=1,
        )
        await self.repo.add_report(report)

        steps = [
            ReportApprovalStep(
                report_id=report.report_id,
                step_number=i + 1,
                approver_id=item["user_id"],
                status="pending",
            )
            for i, item in enumerate(chain)
        ]
        await self.repo.add_steps(steps)
        return report

    async def submit(self, db: AsyncSession, *, report_id: uuid.UUID, actor_id: uuid.UUID) -> Report:
        """draft → approval_pending. 1단계 결재자에게 알림 발행."""
        report = await self._get_or_raise(report_id)
        report = ReportStateMachine.submit(report)

        step = await self.repo.get_current_step(report_id, report.current_step)
        await publish(
            "report.submitted",
            {
                "report_id": str(report_id),
                "step_number": report.current_step,
                "approver_id": str(step.approver_id) if step else None,
            },
        )
        return report

    async def approve(
        self,
        db: AsyncSession,
        *,
        report_id: uuid.UUID,
        actor_id: uuid.UUID,
        decision_text: str,
    ) -> Report:
        """현재 단계 승인. 마지막이면 fully_approved, 아니면 다음 단계로."""
        report = await self._get_or_raise(report_id)
        step = await self._get_current_step_or_raise(report_id, report.current_step)

        if step.approver_id != actor_id:
            raise ValueError("현재 단계의 결재자가 아닙니다.")

        steps = await self.repo.get_steps(report_id)
        is_last = report.current_step >= len(steps)

        step, report = ReportStateMachine.approve_step(
            step, report, actor_id, decision_text, is_last
        )

        if is_last:
            await publish(
                "report.fully_approved",
                {"report_id": str(report_id)},
            )
        else:
            next_step = await self.repo.get_current_step(report_id, report.current_step)
            await publish(
                "report.stage_approved",
                {
                    "report_id": str(report_id),
                    "approved_step": report.current_step - 1,
                    "next_step": report.current_step,
                    "next_approver_id": str(next_step.approver_id) if next_step else None,
                },
            )
        return report

    async def reject(
        self,
        db: AsyncSession,
        *,
        report_id: uuid.UUID,
        actor_id: uuid.UUID,
        decision_text: str,
    ) -> Report:
        """현재 단계 반려 → returned. 기안자에게 이벤트 발행."""
        report = await self._get_or_raise(report_id)
        step = await self._get_current_step_or_raise(report_id, report.current_step)

        if step.approver_id != actor_id:
            raise ValueError("현재 단계의 결재자가 아닙니다.")

        step, report = ReportStateMachine.reject_step(step, report, actor_id, decision_text)

        await publish(
            "report.returned",
            {
                "report_id": str(report_id),
                "rejected_at_step": step.step_number,
                "requester_id": str(report.requester_id),
                "reason": decision_text,
            },
        )
        return report

    async def get_status(self, report_id: uuid.UUID) -> dict:
        """결재선 진행 상태 전체 조회."""
        report = await self._get_or_raise(report_id)
        steps = await self.repo.get_steps(report_id)
        return {
            "report_id": str(report.report_id),
            "title": report.title,
            "status": report.status,
            "current_step": report.current_step,
            "steps": [
                {
                    "step_number": s.step_number,
                    "approver_id": str(s.approver_id),
                    "status": s.status,
                    "decision_text": s.decision_text,
                    "decided_at": s.decided_at.isoformat() if s.decided_at else None,
                }
                for s in steps
            ],
        }

    # ------------------------------------------------------------------
    async def _get_or_raise(self, report_id: uuid.UUID) -> Report:
        report = await self.repo.get_report(report_id)
        if not report:
            raise ValueError(f"Report {report_id} not found")
        return report

    async def _get_current_step_or_raise(
        self, report_id: uuid.UUID, step_number: int
    ) -> ReportApprovalStep:
        step = await self.repo.get_current_step(report_id, step_number)
        if not step:
            raise ValueError(f"결재 단계 {step_number} 를 찾을 수 없습니다.")
        return step
