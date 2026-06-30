import uuid
from datetime import datetime, timezone
from typing import List, Optional

from backend.domains.report.models import Report, ReportApprovalStep
from backend.domains.report.repository import ReportRepository
from backend.domains.report.state_machine import ReportStateMachine
from backend.domains.report.summary_templates import (
    DEFAULT_LOCALE,
    render_key_points,
    render_summary,
    section_title,
)
from backend.infrastructure.event_bus import publish


def _safe_rate(numerator: int, denominator: int) -> int:
    """0-division 방어. 정수 퍼센트 반환."""
    return int(round(100.0 * numerator / denominator)) if denominator else 0


class ReportService:
    def __init__(self, repo: ReportRepository):
        self.repo = repo

    # ── 목록 (3.2a) ─────────────────────────────────────────────

    async def list_reports(
        self,
        tenant_id: Optional[uuid.UUID],
        page: int,
        size: int,
    ) -> List[dict]:
        return await self.repo.list_reports(tenant_id, page, size)

    async def count_reports(self, tenant_id: Optional[uuid.UUID]) -> int:
        return await self.repo.count_reports(tenant_id)

    # ── 리스크 관리 요약 (risk-summary) ─────────────────────────

    def _compute_metrics(self, raw: dict) -> dict:
        """raw 집계 → 프론트 노출 metrics(비율 정수화)."""
        return {
            "supplier_total":       raw["supplier_total"],
            "high_risk_count":      raw["high_risk_count"],
            "audited_suppliers":    raw["audited_suppliers"],
            # audit_decided=0(전부 pending/미판정)이면 pass_rate는 0이 되어 오해를 부르므로
            # 렌더러가 이 값으로 실사 문장 노출 여부를 판단한다.
            "audit_decided":        raw["audit_decided"],
            "audit_pass_rate":      _safe_rate(raw["audit_pass"], raw["audit_decided"]),
            "capa_total":           raw["capa_total"],
            "capa_closed":          raw["capa_closed"],
            "capa_rate":            _safe_rate(raw["capa_closed"], raw["capa_total"]),
            "compliance_pass_rate": _safe_rate(raw["compliance_passed"], raw["compliance_total"]),
            "chain_verified_rate":  _safe_rate(raw["chain_verified"], raw["chain_total"]),
        }

    async def build_risk_summary(
        self,
        tenant_id: Optional[uuid.UUID],
        locale: str = DEFAULT_LOCALE,
    ) -> dict:
        """공급망 리스크 관리 요약문 + metrics 생성. (조회/자동채움 공용)"""
        raw = await self.repo.aggregate_risk_summary(tenant_id)
        metrics = self._compute_metrics(raw)
        return {
            "section_title": section_title(locale),
            "summary_text": render_summary(metrics, locale),
            "key_points": render_key_points(metrics, locale),
            "locale": locale,
            "metrics": metrics,
        }

    # ── 상세 (3.2b) ─────────────────────────────────────────────

    async def get_report_detail(
        self,
        report_id: uuid.UUID,
        tenant_id: Optional[uuid.UUID],
    ) -> dict:
        row = await self.repo.get_report_detail(report_id, tenant_id)
        if not row:
            raise ValueError(f"Report {report_id} not found")

        reject_reason = None
        if row["status"] == "returned":
            reject_reason = await self.repo.get_reject_reason(report_id)

        approval_steps = await self.repo.get_approval_steps_with_users(report_id)

        return {
            **row,
            "reject_reason": reject_reason,
            "approval_steps": approval_steps,
        }

    # ── 생성 (3.2c) ─────────────────────────────────────────────

    async def create_report(
        self,
        *,
        requester_id: uuid.UUID,
        tenant_id: Optional[uuid.UUID],
        title: str,
        type: str,
        batch_id: Optional[uuid.UUID],
        summary: Optional[str],
        approver_ids: List[uuid.UUID],
    ) -> Report:
        """보고서 초안 생성 + 결재선 구성. approver_ids 순서대로 단계 배정.

        summary 미입력 시 공급망 리스크 관리 요약을 자동 생성해 채운다(스냅샷).
        description=요약 본문, key_points=핵심 포인트 bullet.
        """
        if not approver_ids:
            raise ValueError("approverIds 는 최소 1명 이상이어야 합니다.")

        key_points: List[str] = []
        if not summary:
            built = await self.build_risk_summary(tenant_id)
            summary = built["summary_text"]
            key_points = built["key_points"]

        report = Report(
            requester_id=requester_id,
            title=title,
            type=type,
            batch_id=batch_id,
            description=summary,
            key_points=key_points,
            status="draft",
            current_step=1,
        )
        await self.repo.add_report(report)

        steps = [
            ReportApprovalStep(
                report_id=report.report_id,
                step_number=i + 1,
                approver_id=approver_id,
                status="pending",
            )
            for i, approver_id in enumerate(approver_ids)
        ]
        await self.repo.add_steps(steps)

        await self.repo.db.commit()
        return report

    # ── 제출 (3.2d status=submitted) ───────────────────────────

    async def submit(self, *, report_id: uuid.UUID, actor_id: uuid.UUID) -> Report:
        """draft → approval_pending. submitted_at 기록 후 1단계 결재자에게 이벤트."""
        report = await self._get_or_raise(report_id)
        report = ReportStateMachine.submit(report)
        report.submitted_at = datetime.now(timezone.utc)

        step = await self.repo.get_current_step(report_id, report.current_step)

        await self.repo.db.commit()

        await publish(
            "report.submitted",
            {
                "report_id": str(report_id),
                "step_number": report.current_step,
                "approver_id": str(step.approver_id) if step else None,
            },
        )
        return report

    # ── 승인 (3.3b) ─────────────────────────────────────────────

    async def approve(
        self,
        *,
        report_id: uuid.UUID,
        actor_id: uuid.UUID,
        comment: Optional[str],
    ) -> Report:
        """현재 단계 승인. 마지막이면 fully_approved, 아니면 다음 단계로."""
        report = await self._get_or_raise(report_id)
        step = await self._get_current_step_or_raise(report_id, report.current_step)

        if step.approver_id != actor_id:
            raise ValueError("현재 단계의 결재자가 아닙니다.")

        steps = await self.repo.get_steps(report_id)
        is_last = report.current_step >= len(steps)

        step, report = ReportStateMachine.approve_step(
            step, report, actor_id, comment or "", is_last
        )

        await self.repo.db.commit()

        if is_last:
            await publish("report.fully_approved", {"report_id": str(report_id)})
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

    # ── 반려 (3.3c) ─────────────────────────────────────────────

    async def reject(
        self,
        *,
        report_id: uuid.UUID,
        actor_id: uuid.UUID,
        comment: str,
    ) -> Report:
        """현재 단계 반려 → returned. 기안자에게 이벤트 발행."""
        report = await self._get_or_raise(report_id)
        step = await self._get_current_step_or_raise(report_id, report.current_step)

        if step.approver_id != actor_id:
            raise ValueError("현재 단계의 결재자가 아닙니다.")

        step, report = ReportStateMachine.reject_step(step, report, actor_id, comment)

        await self.repo.db.commit()

        await publish(
            "report.returned",
            {
                "report_id": str(report_id),
                "rejected_at_step": step.step_number,
                "requester_id": str(report.requester_id),
                "reason": comment,
            },
        )
        return report

    # ── 결재함 (3.3a) ────────────────────────────────────────────

    async def get_inbox(self, approver_id: uuid.UUID) -> List[dict]:
        rows = await self.repo.get_inbox_rich(approver_id)
        result = []
        for row in rows:
            prev_reviewers = await self.repo.get_previous_reviewers(
                row["report_id"], row["current_step"]
            )
            result.append(
                {
                    "report_id": row["report_id"],
                    "title": row["title"],
                    "status": row["status"],
                    "severity": row["severity"],
                    "submitted_at": row["submitted_at"],
                    "deadline": row["deadline"],
                    "previous_reviewers": prev_reviewers,
                    "key_points": row["key_points"] or [],
                }
            )
        return result

    # ── 내부 헬퍼 ────────────────────────────────────────────────

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
