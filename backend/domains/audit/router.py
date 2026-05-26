# backend/domains/audit/router.py
from datetime import datetime
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import get_db
from backend.domains.audit import service

router = APIRouter(prefix="/audit", tags=["audit"])


class NodeType(str, Enum):
    agent = "agent"
    tool = "tool"
    human = "human"


class AuditTrailRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    audit_id: UUID
    batch_id: UUID | None
    step_number: int | None
    timestamp: datetime | None
    node_type: str | None
    node_name: str | None
    model_version: str | None
    prompt_version: str | None
    duration_ms: int | None
    input_hash: str | None
    output_hash: str | None
    prev_hash: str | None
    decision_text: str | None
    citations: object | None


class ChainBreakOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_number: int | None
    expected_prev_hash: str | None
    actual_prev_hash: str | None
    reason: str


class ChainWarningOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_number: int | None
    reason: str


class ChainVerificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    batch_id: UUID
    total_steps: int
    chain_valid: bool
    breaks: list[ChainBreakOut]
    warnings: list[ChainWarningOut]


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
    """배치의 해시 체인 무결성을 검증해 chain_valid·끊긴 지점·연속성 경고를 반환."""
    return await service.verify_chain(db, batch_id)