import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import get_db
from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.pagination import set_total_count
from backend.domains.dpp.service import (
    list_dpp_records_for_tenant, count_dpp_records_for_tenant,
    get_dpp_status_counts, get_held_products, get_dpp_blockers,
    get_carbon_trend, get_recycled_content_avg, get_readiness_for_frontend,
)
from backend.domains.dpp.delivery_service import generate_delivery_form, record_delivery_history
from backend.domains.dpp.state_machine import issue_dpp, revoke_dpp
from backend.domains.dpp.immutable_guard import ImmutableRecordError
from backend.domains.dpp.models import (
    DppRecordResponse,
    DppRecordBriefOut, DppRecordDetailOut, ReadinessBriefOut,
    HeldProductOut, DppStatusOut, DppBlockersOut,
    CarbonTrendOut, RecycledContentAvgOut, IssueRequest,
)
from backend.domains.dpp.repository import get_dpp_record
from backend.infrastructure.trace import trace_tool

router = APIRouter(prefix="/dpp", tags=["DPP"])


class DeliveryRecordRequest(BaseModel):
    recipient_email: str
    subject: str
    body_text: str


@router.get("/products/{product_id}/readiness", response_model=ReadinessBriefOut,
            dependencies=[Depends(get_current_user)])
async def get_readiness_endpoint(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """[API] GET /dpp/products/{product_id}/readiness — 프론트 계약 shape(checks[], blockers[])."""
    try:
        return await get_readiness_for_frontend(db, product_id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/{dpp_id}/issue", response_model=DppRecordResponse)
async def issue_dpp_endpoint(
    dpp_id: uuid.UUID,
    body: IssueRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] POST /dpp/{dpp_id}/issue — 스펙 body 수신, 승인자는 current_user.user_id로 저장."""
    try:
        record = await issue_dpp(db, dpp_id, approved_by=current_user.user_id)
        return record
    except ImmutableRecordError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# ── §6.2 신규 엔드포인트 ──────────────────────────────────────────────────

@router.get("/status", response_model=DppStatusOut)
async def get_dpp_status_endpoint(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] GET /dpp/status — ready/hold/hitl/blocker/issued 카운트."""
    return await get_dpp_status_counts(db, current_user.tenant_id)


@router.get("/held-products", response_model=list[HeldProductOut])
async def get_held_products_endpoint(
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] GET /dpp/held-products — readiness 미달 제품 목록."""
    items = await get_held_products(db, current_user.tenant_id)
    set_total_count(response, len(items))
    return items


@router.get("/blockers", response_model=DppBlockersOut)
async def get_dpp_blockers_endpoint(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] GET /dpp/blockers — feoc/origin/hitl/audit 블로커 건수."""
    return await get_dpp_blockers(db, current_user.tenant_id)


@router.get("/carbon-footprint/trend", response_model=CarbonTrendOut)
async def get_carbon_trend_endpoint(
    days: int = Query(30, ge=1, le=365),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] GET /dpp/carbon-footprint/trend — 최근 N일 탄소발자국 추이."""
    return await get_carbon_trend(db, current_user.tenant_id, days)


@router.get("/recycled-content/avg", response_model=RecycledContentAvgOut)
async def get_recycled_content_avg_endpoint(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] GET /dpp/recycled-content/avg — Co/Ni/Li 평균 함량."""
    return await get_recycled_content_avg(db, current_user.tenant_id)


@router.get("/products", response_model=list[HeldProductOut])
async def get_products_by_readiness_endpoint(
    response: Response,
    readiness_status: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] GET /dpp/products?readiness_status=hold — 보류 제품 목록(6.3b)."""
    items = await get_held_products(db, current_user.tenant_id)
    if readiness_status == "hold":
        items = [i for i in items if (i.get("readiness") or 0) < 1.0]
    set_total_count(response, len(items))
    return items


@router.post("/{dpp_id}/revoke", response_model=DppRecordResponse,
             dependencies=[Depends(get_current_user)])
async def revoke_dpp_endpoint(
    dpp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    [API] POST /dpp/{dpp_id}/revoke
    DPP를 'dpp_revoked' (폐기) 상태로 전이시켜요.
    """
    try:
        return await revoke_dpp(db, dpp_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

@router.get("/records", response_model=list[DppRecordBriefOut])
@trace_tool("get_dpp_records")
async def get_dpp_records_endpoint(
    response: Response,
    destination: Optional[str] = Query(None),
    approved_by: Optional[uuid.UUID] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] GET /dpp/records — 필터·응답 보강 + X-Total-Count. tenant 격리."""
    from datetime import datetime as dt
    sd = dt.fromisoformat(start_date) if start_date else None
    ed = dt.fromisoformat(end_date) if end_date else None
    items = await list_dpp_records_for_tenant(
        db, current_user.tenant_id, destination, approved_by, status_filter, sd, ed, skip, limit
    )
    total = await count_dpp_records_for_tenant(db, current_user.tenant_id, destination, status_filter)
    set_total_count(response, total)
    return items


@router.get("/records/{dpp_id}", response_model=DppRecordDetailOut)
@trace_tool("get_dpp_record_detail")
async def get_dpp_record_detail_endpoint(
    dpp_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] GET /dpp/records/{dpp_id} — 단건 상세. batches 조인 tenant 격리."""
    from sqlalchemy import text as _text
    record = await get_dpp_record(db, dpp_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DPP 기록을 찾을 수 없습니다.")
    # tenant 격리 — batches 조인 스코프
    tenant_row = (await db.execute(
        _text("SELECT 1 FROM batches WHERE batch_id = :bid AND tenant_id = :tid"),
        {"bid": record.batch_id, "tid": current_user.tenant_id},
    )).fetchone()
    if not tenant_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DPP 기록을 찾을 수 없습니다.")
    payload = record.payload or {}
    section2 = payload.get("annex_xiii_fields", {}).get("section_2_customs_and_goods", {})
    section3 = payload.get("annex_xiii_fields", {}).get("section_3_installation_and_process", {})
    rc = record.recycled_content or {}
    return {
        "dpp_id": record.dpp_id,
        "product_id": record.product_id,
        "status": record.status,
        "issued_at": record.issued_at,
        "carbon_footprint": record.carbon_footprint,
        "approved_by": record.approved_by,
        "recycled_content": {"co": rc.get("Co"), "ni": rc.get("Ni"), "li": rc.get("Li")},
        "serial_number": section2.get("24_commercial_invoice_number"),
        "produced_at_factory_id": section3.get("36_production_installation_id"),
        "produced_at": None,
        "capacity": payload.get("product_info", {}).get("amperage_ah"),
        "supply_chain_version": None,
        "dpp_version": None,
    }


@router.get("/{dpp_id}/delivery-form")
async def get_delivery_form_endpoint(dpp_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    [API] GET /dpp/{dpp_id}/delivery-form
    DPP 발급 데이터를 바탕으로 고객사에 전송할 이메일 양식을 자동 생성하여 반환합니다.
    (customers 조회로 수신처 자동 채움)
    """
    try:
        return await generate_delivery_form(db, dpp_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/{dpp_id}/deliver")
async def record_delivery_endpoint(
    dpp_id: uuid.UUID, 
    req: DeliveryRecordRequest, 
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    [API] POST /dpp/{dpp_id}/deliver
    사용자가 메일/메시지 발송을 완료한 후 전송 이력을 기록합니다.
    """
    try:
        result = await record_delivery_history(
            db=db, 
            dpp_id=dpp_id, 
            recipient_email=req.recipient_email, 
            subject=req.subject, 
            body_text=req.body_text, 
            user_id=current_user.user_id
        )
        await db.commit()
        return result
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))