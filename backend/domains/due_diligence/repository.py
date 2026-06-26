"""
domains/due_diligence/repository.py

Due Diligence 데이터 접근 계층.
기본 테이블: supplier_audit_records
tenant 격리: supplier_audit_records → suppliers.tenant_id JOIN으로 스코프.
flush만 수행 — commit은 service에서 일원화(CLAUDE.md §1).
"""
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.trace import trace_tool


class DueDiligenceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── 5.1 목록 ───────────────────────────────────────────────────────────

    @trace_tool("due_diligence_list")
    async def list_audits(
        self,
        tenant_id: UUID,
        status: Optional[str],
        search: Optional[str],
        page: int,
        size: int,
    ) -> List[Dict[str, Any]]:
        filters = ["s.tenant_id = :tenant_id"]
        params: Dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "offset": (page - 1) * size,
            "size": size,
        }
        if status:
            filters.append("a.audit_status = :status")
            params["status"] = status
        if search:
            filters.append("s.company_name ILIKE :search")
            params["search"] = f"%{search}%"

        where = " AND ".join(filters)
        query = text(f"""
            SELECT
                a.audit_record_id                       AS audit_id,
                a.supplier_id,
                s.company_name                          AS supplier_name,
                a.factory_id,
                a.audit_type                            AS type,
                a.audit_status                          AS status,
                a.result,
                a.score,
                rp.overall_risk_score                   AS risk_score,
                CASE WHEN jsonb_typeof(a.corrective_actions) = 'array'
                     THEN jsonb_array_length(a.corrective_actions) ELSE 0 END AS capa_count,
                (a.report_file_id IS NOT NULL OR a.report_url IS NOT NULL) AS has_report
            FROM supplier_audit_records a
            JOIN suppliers s ON s.supplier_id = a.supplier_id
            LEFT JOIN supplier_risk_profiles rp ON rp.supplier_id = a.supplier_id
            WHERE {where}
            ORDER BY a.created_at DESC
            LIMIT :size OFFSET :offset
        """)
        result = await self.session.execute(query, params)
        return [dict(r._mapping) for r in result]

    @trace_tool("due_diligence_count")
    async def count_audits(
        self,
        tenant_id: UUID,
        status: Optional[str],
        search: Optional[str],
    ) -> int:
        filters = ["s.tenant_id = :tenant_id"]
        params: Dict[str, Any] = {"tenant_id": str(tenant_id)}
        if status:
            filters.append("a.audit_status = :status")
            params["status"] = status
        if search:
            filters.append("s.company_name ILIKE :search")
            params["search"] = f"%{search}%"
        where = " AND ".join(filters)
        query = text(f"""
            SELECT COUNT(*)
            FROM supplier_audit_records a
            JOIN suppliers s ON s.supplier_id = a.supplier_id
            WHERE {where}
        """)
        result = await self.session.execute(query, params)
        return result.scalar() or 0

    # ── 5.2 단건 상세 ───────────────────────────────────────────────────────

    @trace_tool("due_diligence_detail")
    async def get_audit_detail(
        self, audit_id: UUID, tenant_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """5.1 필드 + 상세 필드. 타 테넌트면 None(→404)."""
        query = text("""
            SELECT
                a.audit_record_id                       AS audit_id,
                a.supplier_id,
                s.company_name                          AS supplier_name,
                a.factory_id,
                a.audit_type                            AS type,
                a.audit_status                          AS status,
                a.result,
                a.score,
                rp.overall_risk_score                   AS risk_score,
                CASE WHEN jsonb_typeof(a.corrective_actions) = 'array'
                     THEN jsonb_array_length(a.corrective_actions) ELSE 0 END AS capa_count,
                (a.report_file_id IS NOT NULL OR a.report_url IS NOT NULL) AS has_report,
                a.audit_scope                           AS scope,
                a.auditor                               AS agency,
                a.audit_date::text                      AS completed_at,
                a.findings,
                a.corrective_actions                    AS capa,
                a.report_file_id
            FROM supplier_audit_records a
            JOIN suppliers s ON s.supplier_id = a.supplier_id
            LEFT JOIN supplier_risk_profiles rp ON rp.supplier_id = a.supplier_id
            WHERE a.audit_record_id = :audit_id
              AND s.tenant_id = :tenant_id
        """)
        result = await self.session.execute(
            query, {"audit_id": str(audit_id), "tenant_id": str(tenant_id)}
        )
        row = result.mappings().first()
        return dict(row) if row else None

    # ── 5.3 생성 ────────────────────────────────────────────────────────────

    @trace_tool("due_diligence_create")
    async def create_audit(
        self,
        supplier_id: Optional[UUID],
        factory_id: Optional[UUID],
        audit_name: str,
        audit_scope: str,
    ) -> Dict[str, Any]:
        query = text("""
            INSERT INTO supplier_audit_records
                (supplier_id, factory_id, audit_name, audit_scope, audit_status)
            VALUES
                (:supplier_id, :factory_id, :audit_name, :audit_scope, 'requested')
            RETURNING audit_record_id
        """)
        result = await self.session.execute(query, {
            "supplier_id": str(supplier_id) if supplier_id else None,
            "factory_id": str(factory_id) if factory_id else None,
            "audit_name": audit_name,
            "audit_scope": audit_scope,
        })
        await self.session.flush()
        return {"audit_id": str(result.scalar())}

    # ── 5.4 보고서 업로드 (result / score / report_file_id 갱신) ───────────

    @trace_tool("due_diligence_update_report")
    async def update_report(
        self,
        audit_id: UUID,
        tenant_id: UUID,
        result: Optional[str],
        score: Optional[float],
        report_file_id: Optional[UUID],
    ) -> Optional[Dict[str, Any]]:
        """테넌트 소유 확인 후 보고서 관련 컬럼 갱신. 타 테넌트면 None(→404)."""
        ownership = text("""
            SELECT 1 FROM supplier_audit_records a
            JOIN suppliers s ON s.supplier_id = a.supplier_id
            WHERE a.audit_record_id = :audit_id AND s.tenant_id = :tenant_id
        """)
        exists = await self.session.execute(
            ownership, {"audit_id": str(audit_id), "tenant_id": str(tenant_id)}
        )
        if not exists.first():
            return None

        query = text("""
            UPDATE supplier_audit_records
            SET
                result         = COALESCE(:result, result),
                score          = COALESCE(:score, score),
                report_file_id = COALESCE(:report_file_id, report_file_id)
            WHERE audit_record_id = :audit_id
            RETURNING audit_record_id AS audit_id, result, score, report_file_id
        """)
        res = await self.session.execute(query, {
            "audit_id": str(audit_id),
            "result": result,
            "score": score,
            "report_file_id": str(report_file_id) if report_file_id else None,
        })
        await self.session.flush()
        row = res.mappings().first()
        return dict(row) if row else None

    # ── 5.5 CAPA 상태 갱신 ────────────────────────────────────────────────

    @trace_tool("due_diligence_update_capa")
    async def update_capa_status(
        self,
        audit_id: UUID,
        capa_id: str,
        status: str,
        tenant_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        """corrective_actions JSONB 배열에서 capa_id 항목의 status를 갱신."""
        ownership = text("""
            SELECT 1 FROM supplier_audit_records a
            JOIN suppliers s ON s.supplier_id = a.supplier_id
            WHERE a.audit_record_id = :audit_id AND s.tenant_id = :tenant_id
        """)
        exists = await self.session.execute(
            ownership, {"audit_id": str(audit_id), "tenant_id": str(tenant_id)}
        )
        if not exists.first():
            return None

        query = text("""
            UPDATE supplier_audit_records
            SET corrective_actions = (
                SELECT jsonb_agg(
                    CASE
                        WHEN item->>'capa_id' = :capa_id
                        THEN jsonb_set(item, '{status}', to_jsonb(CAST(:new_status AS text)))
                        ELSE item
                    END
                )
                FROM jsonb_array_elements(
                    CASE WHEN jsonb_typeof(corrective_actions) = 'array'
                         THEN corrective_actions ELSE '[]'::jsonb END
                ) AS item
            )
            WHERE audit_record_id = :audit_id
            RETURNING audit_record_id AS audit_id, corrective_actions AS capa
        """)
        res = await self.session.execute(query, {
            "audit_id": str(audit_id),
            "capa_id": capa_id,
            "new_status": status,
        })
        await self.session.flush()
        row = res.mappings().first()
        return dict(row) if row else None
