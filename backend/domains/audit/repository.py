# backend/domains/audit/repository.py
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.audit.models import AuditTrail


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