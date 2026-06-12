# backend/domains/audit/router.py
from datetime import datetime
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import get_db
from backend.domains.audit import service
from backend.domains.audit.service import BatchNotFound
from backend.domains.audit.models import AuditTrailRow, ChainVerificationOut

router = APIRouter(prefix="/audit", tags=["audit"])
actions_router = APIRouter(tags=["actions"])


class NodeType(str, Enum):
    agent = "agent"
    tool = "tool"
    human = "human"


class ActionStatus(str, Enum):
    open = "open"
    sent = "sent"
    review = "review"
    resolved = "resolved"
    blocked = "blocked"


class ActionSourceType(str, Enum):
    SUB = "SUB"
    DD = "DD"
    HITL = "HITL"


class CurrentUser(BaseModel):
    user_id: UUID


class ActionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action_id: str
    source_type: str
    title: str
    supplier_id: UUID | None
    assigned_to: UUID | None
    due_date: datetime | None
    action_status: str


class GapAnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    affected_supplier_ids: object | None
    newly_required_fields: object | None


async def get_current_user(x_user_id: UUID = Header(alias="X-User-Id")) -> CurrentUser:
    return CurrentUser(user_id=x_user_id)


@actions_router.get("/actions", response_model=list[ActionItemOut])
async def get_actions(
    status: ActionStatus | None = Query(default=None),
    source_type: ActionSourceType | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_action_items(
        db,
        status=status.value if status is not None else None,
        source_type=source_type.value if source_type is not None else None,
    )


@actions_router.get("/actions/mine", response_model=list[ActionItemOut])
async def get_my_actions(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_my_action_items(db, current_user.user_id)


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


@router.get("/gap-analysis/{regulation_id}", response_model=list[GapAnalysisOut])
async def get_gap_analysis(
    regulation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    return await service.get_gap_analysis_results(db, regulation_id)
