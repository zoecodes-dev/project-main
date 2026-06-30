# backend/domains/audit/repository.py
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.audit.models import AuditTrail


async def batch_exists(db: AsyncSession, batch_id: UUID) -> bool:
    """
    batches 에 해당 batch_id 가 실재하는지 확인.
    audit_trail 이 FK로 참조하는 공용 배치 테이블을 읽는 것이므로
    다른 도메인 ORM 을 import 하지 않고 raw 조회로 처리한다.
    """
    stmt = text("SELECT 1 FROM batches WHERE batch_id = :bid LIMIT 1")
    result = await db.execute(stmt, {"bid": str(batch_id)})
    return result.first() is not None


async def list_trail_by_batch(
    db: AsyncSession,
    batch_id: UUID,
    node_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[AuditTrail]:
    """
    한 배치의 audit_trail을 step_number 오름차순으로 반환.
    node_type / 기간(start~end) 필터는 선택. 정렬은 항상 step_number asc.
    인덱스 idx_audit_batch(batch_id, step_number)를 그대로 탄다.
    """
    stmt = select(AuditTrail).where(AuditTrail.batch_id == batch_id)

    if node_type is not None:
        stmt = stmt.where(AuditTrail.node_type == node_type)
    if start is not None:
        stmt = stmt.where(AuditTrail.timestamp >= start)
    if end is not None:
        stmt = stmt.where(AuditTrail.timestamp <= end)

    stmt = stmt.order_by(AuditTrail.step_number.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_full_chain(db: AsyncSession, batch_id: UUID) -> list[AuditTrail]:
    """
    해시 체인 검증 전용 — 배치의 모든 row를 step_number 순으로(필터 없이) 반환.
    체인 검증은 누락 없는 전체 시퀀스 위에서만 의미가 있으므로 필터를 받지 않는다.
    """
    stmt = (
        select(AuditTrail)
        .where(AuditTrail.batch_id == batch_id)
        .order_by(AuditTrail.step_number.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_action_items(
    db: AsyncSession,
    status: str | None = None,
    source_type: str | None = None,
    assigned_to: UUID | None = None,
    unresolved_only: bool = False,
) -> list[dict]:
    stmt = """
        SELECT
            action_id,
            source_type,
            title,
            supplier_id,
            assigned_to,
            due_date,
            action_status
        FROM v_action_items
        WHERE (CAST(:status AS text) IS NULL OR action_status = CAST(:status AS text))
          AND (CAST(:source_type AS text) IS NULL OR source_type = CAST(:source_type AS text))
          AND (CAST(:assigned_to AS uuid) IS NULL OR assigned_to = CAST(:assigned_to AS uuid))
          AND (:unresolved_only = FALSE OR action_status != 'resolved')
        ORDER BY due_date ASC NULLS LAST, action_id ASC
    """
    result = await db.execute(
        text(stmt),
        {
            "status": status,
            "source_type": source_type,
            "assigned_to": str(assigned_to) if assigned_to is not None else None,
            "unresolved_only": unresolved_only,
        },
    )
    return [dict(row._mapping) for row in result.all()]


async def list_gap_analysis_results(db: AsyncSession, regulation_id: UUID) -> list[dict]:
    stmt = text(
        """
        SELECT
            affected_supplier_ids,
            newly_required_fields
        FROM gap_analysis_results
        WHERE regulation_id = CAST(:regulation_id AS uuid)
        ORDER BY analyzed_at DESC
        """
    )
    result = await db.execute(stmt, {"regulation_id": str(regulation_id)})
    return [dict(row._mapping) for row in result.all()]


# ── audit packages (2.5b · 2.5c) ───────────────────────────────────────────

async def list_audit_packages(
    db: AsyncSession,
    tenant_id: UUID | None,
    page: int,
    size: int,
) -> list[dict]:
    """
    batches 를 audit package 단위로 집계.
    evidenceCount = audit_data_snapshots 건수.
    gapCount      = compliance_results 중 compliance_passed 가 아닌 건수.
    """
    stmt = text(
        """
        SELECT
            b.batch_id                                                     AS package_id,
            COALESCE(p.name, b.batch_id::text)                            AS target,
            b.destination                                                   AS type,
            b.status,
            COUNT(DISTINCT ads.snapshot_id)                                AS evidence_count,
            COUNT(DISTINCT cr.result_id)
                FILTER (WHERE cr.verdict != 'compliance_passed')           AS gap_count,
            COALESCE(t.company_name, '')                                   AS owner,
            b.received_at                                                  AS created_at
        FROM batches b
        LEFT JOIN products p        ON p.product_id  = b.product_id
        LEFT JOIN tenants  t        ON t.tenant_id   = b.tenant_id
        LEFT JOIN audit_data_snapshots ads ON ads.batch_id = b.batch_id
        LEFT JOIN compliance_results   cr  ON cr.batch_id  = b.batch_id
        WHERE (CAST(:tenant_id AS uuid) IS NULL OR b.tenant_id = CAST(:tenant_id AS uuid))
        GROUP BY b.batch_id, p.name, b.destination, b.status, t.company_name, b.received_at
        ORDER BY b.received_at DESC
        LIMIT :size OFFSET :offset
        """
    )
    result = await db.execute(
        stmt,
        {
            "tenant_id": str(tenant_id) if tenant_id else None,
            "size": size,
            "offset": (page - 1) * size,
        },
    )
    return [dict(row._mapping) for row in result.all()]


async def count_audit_packages(db: AsyncSession, tenant_id: UUID | None) -> int:
    stmt = text(
        """
        SELECT COUNT(*)
        FROM batches
        WHERE (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = CAST(:tenant_id AS uuid))
        """
    )
    result = await db.execute(
        stmt, {"tenant_id": str(tenant_id) if tenant_id else None}
    )
    return result.scalar_one()


async def get_audit_package(
    db: AsyncSession, package_id: UUID, tenant_id: UUID | None
) -> dict | None:
    stmt = text(
        """
        SELECT
            b.batch_id                                                     AS package_id,
            COALESCE(p.name, b.batch_id::text)                            AS target,
            b.destination                                                   AS type,
            b.status,
            COUNT(DISTINCT ads.snapshot_id)                                AS evidence_count,
            COUNT(DISTINCT cr.result_id)
                FILTER (WHERE cr.verdict != 'compliance_passed')           AS gap_count,
            COALESCE(t.company_name, '')                                   AS owner,
            b.received_at                                                  AS created_at
        FROM batches b
        LEFT JOIN products p        ON p.product_id  = b.product_id
        LEFT JOIN tenants  t        ON t.tenant_id   = b.tenant_id
        LEFT JOIN audit_data_snapshots ads ON ads.batch_id = b.batch_id
        LEFT JOIN compliance_results   cr  ON cr.batch_id  = b.batch_id
        WHERE b.batch_id = CAST(:package_id AS uuid)
          AND (CAST(:tenant_id AS uuid) IS NULL OR b.tenant_id = CAST(:tenant_id AS uuid))
        GROUP BY b.batch_id, p.name, b.destination, b.status,
                 t.company_name, b.received_at
        """
    )
    result = await db.execute(
        stmt,
        {
            "package_id": str(package_id),
            "tenant_id": str(tenant_id) if tenant_id else None,
        },
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def list_package_trail(db: AsyncSession, package_id: UUID) -> list[dict]:
    """audit_trail rows → AuditTrailItem 형태로 반환 (snake_case; 프론트 camelCase 변환)."""
    stmt = text(
        """
        SELECT
            step_number,
            timestamp,
            node_type,
            node_name,
            model_version  AS model,
            prompt_version,
            duration_ms,
            input_hash,
            output_hash,
            decision_text  AS decision,
            COALESCE(citations, '[]'::jsonb) AS citations
        FROM audit_trail
        WHERE batch_id = CAST(:package_id AS uuid)
        ORDER BY step_number ASC
        """
    )
    result = await db.execute(stmt, {"package_id": str(package_id)})
    return [dict(row._mapping) for row in result.all()]


async def list_audit_snapshots(db: AsyncSession, package_id: UUID) -> list[dict]:
    """audit_data_snapshots — 승인 순간 동결된 증빙 스냅샷.
    snapshot_data(active BOM·협력사·규제판정 동결 JSON) 원본을 포함해 반환한다.
    부인방지 핵심 증빙이라 export 번들에 그대로 싣는다."""
    stmt = text(
        """
        SELECT
            snapshot_id,
            step_id,
            decided_by,
            snapshot_data,
            signature_hash,
            created_at
        FROM audit_data_snapshots
        WHERE batch_id = CAST(:package_id AS uuid)
        ORDER BY created_at ASC
        """
    )
    result = await db.execute(stmt, {"package_id": str(package_id)})
    return [dict(row._mapping) for row in result.all()]


async def create_pending_hitl_review(
    db: AsyncSession,
    batch_id: UUID,
    reason: str,
    trigger_stage: str,
) -> tuple[UUID, bool]:
    """
    Create one pending HITL review for an interrupted stage.

    LangGraph restarts an interrupted node from its beginning on resume, so the
    lookup keeps that replay from inserting a duplicate review row.
    """
    stmt = text(
        """
        WITH existing AS (
            SELECT review_id
            FROM hitl_reviews
            WHERE batch_id = :batch_id
              AND reason = :reason
              AND trigger_stage = :trigger_stage
              AND status IN ('hitl_pending', 'hitl_in_review')
            ORDER BY created_at DESC
            LIMIT 1
        ),
        inserted AS (
            INSERT INTO hitl_reviews (batch_id, reason, trigger_stage, status)
            SELECT :batch_id, :reason, :trigger_stage, 'hitl_pending'
            WHERE NOT EXISTS (SELECT 1 FROM existing)
            RETURNING review_id
        )
        SELECT review_id, TRUE AS created FROM inserted
        UNION ALL
        SELECT review_id, FALSE AS created FROM existing
        LIMIT 1
        """
    )
    result = await db.execute(
        stmt,
        {
            "batch_id": str(batch_id),
            "reason": reason,
            "trigger_stage": trigger_stage,
        },
    )
    row = result.one()
    return row.review_id, row.created
