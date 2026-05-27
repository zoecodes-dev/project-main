# backend/domains/audit/router.py
from datetime import datetime
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import get_db
from backend.domains.audit import service
from backend.domains.audit.service import BatchNotFound
from backend.domains.audit.models import AuditTrailRow, ChainVerificationOut

router = APIRouter(prefix="/audit", tags=["audit"])


class NodeType(str, Enum):
    agent = "agent"
    tool = "tool"
    human = "human"


@router.get("/trail/{batch_id}", response_model=list[AuditTrailRow])
async def get_audit_trail(
    batch_id: UUID,
    node_type: NodeType | None = Query(default=None),
    start: datetime | None = Query(default=None, description="timestamp >= start (ISO 8601)"),
    end: datetime | None = Query(default=None, description="timestamp <= end (ISO 8601)"),
    db: AsyncSession = Depends(get_db),
):
    """배치의 audit_trail을 step_number 순으로 반환. node_type/기간 필터 선택."""
    nt = node_type.value if node_type is not None else None
    return await service.get_trail(db, batch_id, nt, start, end)


@router.get("/trail/{batch_id}/verify", response_model=ChainVerificationOut)
async def verify_audit_chain(
    batch_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """배치의 해시 체인 무결성을 검증. 없는 batch_id 는 404."""
    try:
        return await service.verify_chain(db, batch_id)
    except BatchNotFound:
        raise HTTPException(status_code=404, detail=f"batch not found: {batch_id}")