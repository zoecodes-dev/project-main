from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.infrastructure.database import get_db
from backend.domains.risk.service import calculate_risk_score


router = APIRouter(prefix="/risk", tags=["Risk"])

class ViolationItem(BaseModel):
    type: str  # 'compliance_violation', 'compliance_reject', 'GeoRiskDetected', 'compliance_warning'
    reason: str

class StageRiskRequest(BaseModel):
    batch_id: UUID
    supplier_id: UUID
    violations: list[ViolationItem]


@router.post("/stage-risk")
async def execute_stage_risk(req: StageRiskRequest, db: AsyncSession = Depends(get_db)):
    result = await calculate_risk_score(
        db=db,
        batch_id=req.batch_id,
        supplier_id=req.supplier_id,
        violations=[v.model_dump() for v in req.violations]
    )
    return result
