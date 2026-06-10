import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import get_db
from backend.domains.dpp.service import calculate_readiness, generate_dpp_payload, create_dpp_record
from backend.domains.dpp.state_machine import issue_dpp, revoke_dpp
from backend.domains.dpp.immutable_guard import ImmutableRecordError
from backend.domains.dpp.models import DppRecordResponse, ReadinessResponse
from backend.domains.dpp.repository import list_dpp_records_raw, get_dpp_record
from backend.infrastructure.trace import trace_tool

router = APIRouter(prefix="/dpp", tags=["DPP"])


@router.get("/products/{product_id}/readiness", response_model=ReadinessResponse)
async def get_readiness_endpoint(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    [API] GET /dpp/products/{product_id}/readiness
    제품의 8대 체크리스트를 기반으로 DPP 발행 준비도(Readiness)를 계산해요.
    """
    try:
        return await calculate_readiness(db, product_id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/{dpp_id}/issue", response_model=DppRecordResponse)
async def issue_dpp_endpoint(
    dpp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    [API] POST /dpp/{dpp_id}/issue
    DPP를 'dpp_issued' 상태로 발행해요.
    (발행 후에는 이중 가드가 작동해서 절대 수정할 수 없게 돼요.)
    """
    try:
        return await issue_dpp(db, dpp_id)
    except ImmutableRecordError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/{dpp_id}/revoke", response_model=DppRecordResponse)
async def revoke_dpp_endpoint(
    dpp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    [API] POST /dpp/{dpp_id}/revoke
    DPP를 'dpp_revoked' (폐기) 상태로 전이시켜요.
    """
    try:
        return await revoke_dpp(db, dpp_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

@router.get("/records")
@trace_tool("get_dpp_records")
async def get_dpp_records_endpoint(customer_id: uuid.UUID | None = None, db: AsyncSession = Depends(get_db)):
    """
    [API] GET /dpp/records
    고객사별 전체 DPP 발행 이력 조회(목록)를 반환합니다.
    """
    return await list_dpp_records_raw(db, customer_id)

@router.get("/records/{dpp_id}", response_model=DppRecordResponse)
@trace_tool("get_dpp_record_detail")
async def get_dpp_record_detail_endpoint(dpp_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    [API] GET /dpp/records/{dpp_id}
    DPP Payload와 80대 필드가 담긴 상세 여권 기록을 반환합니다.
    """
    record = await get_dpp_record(db, dpp_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DPP 기록을 찾을 수 없습니다.")
    return record