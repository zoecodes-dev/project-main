import uuid
from datetime import datetime, timezone

from backend.domains.report.models import Report, ReportApprovalStep


class ReportStateMachine:

    @staticmethod
    def submit(report: Report) -> Report:
        """draft → approval_pending. 기안자가 결재선에 올리는 시점."""
        if report.status != "draft":
            raise ValueError(f"submit 은 draft 상태에서만 가능합니다. 현재: {report.status}")
        report.status = "approval_pending"
        report.current_step = 1
        report.updated_at = datetime.now(timezone.utc)
        return report

    @staticmethod
    def approve_step(
        step: ReportApprovalStep,
        report: Report,
        actor_id: uuid.UUID,
        decision_text: str,
        is_last_step: bool,
    ) -> tuple[ReportApprovalStep, Report]:
        """
        현재 단계 승인.
        - 마지막 단계면 report → fully_approved.
        - 아니면 report.current_step 증가 (다음 단계로).
        actor_id 는 호출부에서 검증(assignee 일치) 후 전달.
        """
        if report.status != "approval_pending":
            raise ValueError(f"approve 는 approval_pending 상태에서만 가능합니다. 현재: {report.status}")
        if step.status != "pending":
            raise ValueError(f"이미 처리된 결재 단계입니다. 현재: {step.status}")

        now = datetime.now(timezone.utc)
        step.status = "approved"
        step.decision_text = decision_text
        step.decided_at = now

        if is_last_step:
            report.status = "fully_approved"
        else:
            report.current_step += 1

        report.updated_at = now
        return step, report

    @staticmethod
    def reject_step(
        step: ReportApprovalStep,
        report: Report,
        actor_id: uuid.UUID,
        decision_text: str,
    ) -> tuple[ReportApprovalStep, Report]:
        """어느 단계든 반려 → report returned, 기안자에게 돌아감."""
        if report.status != "approval_pending":
            raise ValueError(f"reject 는 approval_pending 상태에서만 가능합니다. 현재: {report.status}")
        if step.status != "pending":
            raise ValueError(f"이미 처리된 결재 단계입니다. 현재: {step.status}")

        now = datetime.now(timezone.utc)
        step.status = "rejected"
        step.decision_text = decision_text
        step.decided_at = now

        report.status = "returned"
        report.updated_at = now
        return step, report
