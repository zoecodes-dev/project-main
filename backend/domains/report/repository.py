import uuid
from typing import List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.report.models import Report, ReportApprovalStep


class ReportRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── 기존 단건 조회 ──────────────────────────────────────────

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

    async def get_current_step(
        self, report_id: uuid.UUID, step_number: int
    ) -> Optional[ReportApprovalStep]:
        result = await self.db.execute(
            select(ReportApprovalStep).where(
                ReportApprovalStep.report_id == report_id,
                ReportApprovalStep.step_number == step_number,
            )
        )
        return result.scalar_one_or_none()

    # ── 목록 (3.2a) ─────────────────────────────────────────────

    async def list_reports(
        self,
        tenant_id: Optional[uuid.UUID],
        page: int,
        size: int,
    ) -> List[dict]:
        stmt = text(
            """
            SELECT
                r.report_id,
                r.type,
                r.title,
                u.name    AS author,
                u.role    AS author_role,
                r.batch_id AS related_batch,
                r.submitted_at,
                r.status
            FROM reports r
            JOIN users u ON r.requester_id = u.user_id
            WHERE (CAST(:tenant_id AS uuid) IS NULL OR u.tenant_id = CAST(:tenant_id AS uuid))
            ORDER BY r.created_at DESC
            LIMIT :size OFFSET :offset
            """
        )
        result = await self.db.execute(
            stmt,
            {
                "tenant_id": str(tenant_id) if tenant_id else None,
                "size": size,
                "offset": (page - 1) * size,
            },
        )
        return [dict(row._mapping) for row in result.all()]

    async def count_reports(self, tenant_id: Optional[uuid.UUID]) -> int:
        stmt = text(
            """
            SELECT COUNT(*)
            FROM reports r
            JOIN users u ON r.requester_id = u.user_id
            WHERE (CAST(:tenant_id AS uuid) IS NULL OR u.tenant_id = CAST(:tenant_id AS uuid))
            """
        )
        result = await self.db.execute(
            stmt, {"tenant_id": str(tenant_id) if tenant_id else None}
        )
        return result.scalar_one()

    # ── 상세 (3.2b) ─────────────────────────────────────────────

    async def get_report_detail(
        self, report_id: uuid.UUID, tenant_id: Optional[uuid.UUID]
    ) -> Optional[dict]:
        stmt = text(
            """
            SELECT
                r.report_id,
                r.type,
                r.title,
                u.name    AS author,
                u.role    AS author_role,
                r.batch_id AS related_batch,
                r.submitted_at,
                r.status,
                r.description AS summary
            FROM reports r
            JOIN users u ON r.requester_id = u.user_id
            WHERE r.report_id = CAST(:report_id AS uuid)
              AND (CAST(:tenant_id AS uuid) IS NULL OR u.tenant_id = CAST(:tenant_id AS uuid))
            """
        )
        result = await self.db.execute(
            stmt,
            {
                "report_id": str(report_id),
                "tenant_id": str(tenant_id) if tenant_id else None,
            },
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_reject_reason(self, report_id: uuid.UUID) -> Optional[str]:
        stmt = text(
            """
            SELECT decision_text
            FROM report_approval_steps
            WHERE report_id = CAST(:report_id AS uuid) AND status = 'rejected'
            ORDER BY decided_at DESC
            LIMIT 1
            """
        )
        result = await self.db.execute(stmt, {"report_id": str(report_id)})
        row = result.scalar_one_or_none()
        return row

    async def get_approval_steps_with_users(self, report_id: uuid.UUID) -> List[dict]:
        stmt = text(
            """
            SELECT
                s.step_number,
                s.status,
                s.decided_at,
                u.name AS approver,
                u.role
            FROM report_approval_steps s
            JOIN users u ON s.approver_id = u.user_id
            WHERE s.report_id = CAST(:report_id AS uuid)
            ORDER BY s.step_number
            """
        )
        result = await self.db.execute(stmt, {"report_id": str(report_id)})
        return [dict(row._mapping) for row in result.all()]

    # ── 결재함 (3.3a) ────────────────────────────────────────────

    async def get_inbox_rich(self, approver_id: uuid.UUID) -> List[dict]:
        stmt = text(
            """
            SELECT
                r.report_id,
                r.title,
                r.status,
                r.severity,
                r.submitted_at,
                r.deadline,
                r.key_points,
                r.current_step
            FROM report_approval_steps s
            JOIN reports r ON s.report_id = r.report_id
            WHERE s.approver_id = CAST(:approver_id AS uuid)
              AND s.status = 'pending'
            ORDER BY r.deadline NULLS LAST, r.submitted_at
            """
        )
        result = await self.db.execute(
            stmt, {"approver_id": str(approver_id)}
        )
        return [dict(row._mapping) for row in result.all()]

    async def get_previous_reviewers(
        self, report_id: uuid.UUID, before_step: int
    ) -> List[uuid.UUID]:
        stmt = text(
            """
            SELECT approver_id
            FROM report_approval_steps
            WHERE report_id = CAST(:report_id AS uuid)
              AND step_number < :before_step
              AND status != 'pending'
            ORDER BY step_number
            """
        )
        result = await self.db.execute(
            stmt, {"report_id": str(report_id), "before_step": before_step}
        )
        return [row[0] for row in result.all()]

    # ── 기존 결재함 (단순 버전, 하위 호환) ─────────────────────

    async def get_inbox(self, approver_id: uuid.UUID) -> List[ReportApprovalStep]:
        result = await self.db.execute(
            select(ReportApprovalStep).where(
                ReportApprovalStep.approver_id == approver_id,
                ReportApprovalStep.status == "pending",
            )
        )
        return list(result.scalars().all())

    # ── 리스크 요약 집계 (risk-summary) ───────────────────────────

    async def aggregate_risk_summary(
        self, tenant_id: Optional[uuid.UUID]
    ) -> dict:
        """
        공급망 리스크 관리 요약용 raw 집계. 비율 계산/문장화는 service에서 수행.
        tenant_id=None(관리 토큰)이면 전체 집계, 아니면 해당 테넌트로 격리(§0.2).
        compliance/chain은 tenant_id 컬럼이 없어 batches / suppliers 조인으로 스코프.
        """
        tid = str(tenant_id) if tenant_id else None

        # ① 협력사 수 + 고위험(high/critical) 수
        sup = (await self.db.execute(text(
            """
            SELECT
                COUNT(*)                                                         AS supplier_total,
                COUNT(*) FILTER (WHERE rp.risk_level IN ('high', 'critical'))     AS high_risk_count
            FROM suppliers s
            LEFT JOIN supplier_risk_profiles rp ON rp.supplier_id = s.supplier_id
            WHERE (CAST(:tid AS uuid) IS NULL OR s.tenant_id = CAST(:tid AS uuid))
            """
        ), {"tid": tid})).mappings().one()

        # ② 실사 수행 협력사 수 + 적합 판정(pass/conditional_pass) — pending/null 제외
        aud = (await self.db.execute(text(
            """
            SELECT
                COUNT(DISTINCT a.supplier_id)                                    AS audited_suppliers,
                COUNT(*) FILTER (WHERE a.result IN ('pass', 'conditional_pass'))  AS audit_pass,
                COUNT(*) FILTER (WHERE a.result IN ('pass', 'conditional_pass', 'fail')) AS audit_decided
            FROM supplier_audit_records a
            JOIN suppliers s ON s.supplier_id = a.supplier_id
            WHERE (CAST(:tid AS uuid) IS NULL OR s.tenant_id = CAST(:tid AS uuid))
            """
        ), {"tid": tid})).mappings().one()

        # ③ 시정조치(CAPA) — corrective_actions JSONB 배열을 펼쳐 완료 비율 산출.
        #    status는 JSONB 내부 자유문자열이라 완료 후보값을 넓게 잡는다.
        capa = (await self.db.execute(text(
            """
            SELECT
                COUNT(*)                                                         AS capa_total,
                COUNT(*) FILTER (
                    WHERE lower(item->>'status')
                          IN ('closed', 'completed', 'resolved', 'verified', 'done')
                )                                                                AS capa_closed
            FROM supplier_audit_records a
            JOIN suppliers s ON s.supplier_id = a.supplier_id
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(a.corrective_actions) = 'array'
                     THEN a.corrective_actions ELSE '[]'::jsonb END
            ) AS item
            WHERE (CAST(:tid AS uuid) IS NULL OR s.tenant_id = CAST(:tid AS uuid))
            """
        ), {"tid": tid})).mappings().one()

        # ④ 컴플라이언스 통과율 (기존 dashboard 패턴 재사용)
        comp = (await self.db.execute(text(
            """
            SELECT
                COUNT(*)                                                         AS compliance_total,
                COUNT(*) FILTER (WHERE cr.verdict = 'compliance_passed')          AS compliance_passed
            FROM compliance_results cr
            JOIN batches b ON b.batch_id = cr.batch_id
            WHERE (CAST(:tid AS uuid) IS NULL OR b.tenant_id = CAST(:tid AS uuid))
            """
        ), {"tid": tid})).mappings().one()

        # ⑤ 공급망 연결 검증율 (parent_supplier → suppliers.tenant_id 로 스코프)
        chain = (await self.db.execute(text(
            """
            SELECT
                COUNT(*)                                                         AS chain_total,
                COUNT(*) FILTER (WHERE scm.verification_status = 'verified')      AS chain_verified
            FROM supply_chain_map scm
            JOIN suppliers s ON s.supplier_id = scm.parent_supplier_id
            WHERE (CAST(:tid AS uuid) IS NULL OR s.tenant_id = CAST(:tid AS uuid))
            """
        ), {"tid": tid})).mappings().one()

        return {
            "supplier_total":    int(sup["supplier_total"] or 0),
            "high_risk_count":   int(sup["high_risk_count"] or 0),
            "audited_suppliers": int(aud["audited_suppliers"] or 0),
            "audit_pass":        int(aud["audit_pass"] or 0),
            "audit_decided":     int(aud["audit_decided"] or 0),
            "capa_total":        int(capa["capa_total"] or 0),
            "capa_closed":       int(capa["capa_closed"] or 0),
            "compliance_total":  int(comp["compliance_total"] or 0),
            "compliance_passed": int(comp["compliance_passed"] or 0),
            "chain_total":       int(chain["chain_total"] or 0),
            "chain_verified":    int(chain["chain_verified"] or 0),
        }

    # ── 쓰기 ──────────────────────────────────────────────────────

    async def add_report(self, report: Report) -> Report:
        self.db.add(report)
        await self.db.flush()
        return report

    async def add_steps(self, steps: List[ReportApprovalStep]) -> None:
        for step in steps:
            self.db.add(step)
        await self.db.flush()
