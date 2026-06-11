import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db
from backend.hitl.repository import HitlRepository
from backend.hitl.service import HitlService

router = APIRouter(prefix="/hitl", tags=["HITL"])

class ResolveRequest(BaseModel):
    resolution: str
    decision_text: str

class DecisionRequest(BaseModel):
    decision_text: str

def get_hitl_service(db: AsyncSession = Depends(get_db)) -> HitlService:
    return HitlService(HitlRepository(db))

# 1. 미처리 보류 건 목록 조회
@router.get("/queue")
async def get_hitl_queue(status: str = 'hitl_pending', service: HitlService = Depends(get_hitl_service)):
    if status != 'hitl_pending':
        raise HTTPException(status_code=400, detail="Currently only hitl_pending status is supported for queue")
    return await service.get_pending_queue()

# 4. 검토에 필요한 모든 컨텍스트 단일 JSON 조회 (순서를 위해 위로 올렸어요)
@router.get("/{batch_id}/context")
async def get_hitl_context(batch_id: uuid.UUID, service: HitlService = Depends(get_hitl_service)):
    try:
        return await service.get_review_context(batch_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

# 2. 범용 Resolve 엔드포인트
@router.post("/{batch_id}/resolve")
async def resolve_hitl_review(
    batch_id: uuid.UUID,
    request: ResolveRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: HitlService = Depends(get_hitl_service),
    db: AsyncSession = Depends(get_db),
):
    try:
        review = await service.resolve_batch(
            db,
            batch_id=batch_id,
            resolution=request.resolution,
            decision_text=request.decision_text,
            user_id=current_user.user_id,
        )
        await db.commit() # 트랜잭션 확정
        return {"status": "success", "review_id": review.review_id, "resolution": review.resolution}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# 3. 승인(Approve) 명시적 엔드포인트
@router.post("/{batch_id}/approve")
async def approve_hitl_review(
    batch_id: uuid.UUID,
    request: DecisionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: HitlService = Depends(get_hitl_service),
    db: AsyncSession = Depends(get_db),
):
    try:
        review = await service.resolve_batch(
            db,
            batch_id=batch_id,
            resolution="approve",
            decision_text=request.decision_text,
            user_id=current_user.user_id,
        )
        await db.commit()
        return {"status": "success", "review_id": review.review_id, "resolution": "approve"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# 3. 반려(Reject) 명시적 엔드포인트
@router.post("/{batch_id}/reject")
async def reject_hitl_review(
    batch_id: uuid.UUID,
    request: DecisionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: HitlService = Depends(get_hitl_service),
    db: AsyncSession = Depends(get_db),
):
    try:
        review = await service.resolve_batch(
            db,
            batch_id=batch_id,
            resolution="reject",
            decision_text=request.decision_text,
            user_id=current_user.user_id,
        )
        await db.commit()
        return {"status": "success", "review_id": review.review_id, "resolution": "reject"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
