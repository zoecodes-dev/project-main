"""
domains/supplier/router.py  (담당: 팀원 B)

Supplier 도메인 HTTP 진입점(얇은 라우팅 레이어).
- 비즈니스 로직·커밋·이벤트 발행은 service가 담당. router는 요청 수신/응답만.
- 커밋은 service에서 일원화한다. ★ router에서 db.commit() 하지 않는다.
  (service.create_supplier_and_invite가 커밋 후 발행까지 책임진다)
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import get_db
from backend.domains.supplier import service

router = APIRouter(prefix="/suppliers", tags=["Suppliers"])


# ── 요청/응답 스키마 ────────────────────────────────────────
class SupplierCreateRequest(BaseModel):
    tenant_id: UUID
    company_name: str
    supplier_type: str
    email: str


class SupplierBrief(BaseModel):
    """
    목록·단건 응답용 직렬화 스키마.
    ORM 객체를 그대로 반환하면 CTI relationship lazy load에서 직렬화 에러가
    날 수 있으므로, 명시적 스키마로 변환해 반환한다(직렬화 안전).
    from_attributes=True 로 ORM 인스턴스에서 바로 만든다.
    """
    supplier_id: UUID
    company_name: str
    supplier_type: str
    tier: Optional[int] = None
    status: str
    risk_level: str

    model_config = {"from_attributes": True}

class RiskProfileResponse(BaseModel):
    supplier_id: UUID
    overall_risk_score: int
    risk_level: str
    feoc_status: str
 
    model_config = {"from_attributes": True}
 
class RiskScoreUpdateRequest(BaseModel):
    score: int  # 0~100, 높을수록 위험

@router.post("", status_code=201)
async def create_supplier_endpoint(
    request: SupplierCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """협력사 등록 및 초대 이벤트 발행. (커밋·발행은 service가 처리)"""
    supplier_data = {
        "tenant_id": request.tenant_id,
        "company_name": request.company_name,
        "supplier_type": request.supplier_type,
    }
    supplier = await service.create_supplier_and_invite(
        db, supplier_data, request.email
    )
    # ★ 여기서 db.commit() 하지 않는다 — service가 이미 커밋했다.
    return {"supplier_id": supplier.supplier_id, "status": supplier.status}


@router.get("/{supplier_id}", response_model=SupplierBrief)
async def get_supplier_endpoint(
    supplier_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """협력사 단건 상세 조회."""
    supplier = await service.get_supplier(db, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier  # response_model(SupplierBrief)이 ORM→스키마 변환


@router.get("", response_model=List[SupplierBrief])
async def list_suppliers_endpoint(
    status: Optional[str] = None,
    tier: Optional[int] = None,
    risk_level: Optional[str] = None,
    feoc_status: Optional[str] = None,
    page: int = 1,
    size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """협력사 목록 필터링 조회 (status / tier / risk_level / feoc_status + 페이지)."""
    return await service.list_suppliers(
        db, status, tier, risk_level, feoc_status, page, size
    )
 
  

 
 
@router.get("/{supplier_id}/risk-profile", response_model=RiskProfileResponse)
async def get_risk_profile_endpoint(
    supplier_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """협력사 리스크 프로필 조회."""
    profile = await service.get_risk_profile(supplier_id, db)
    if not profile:
        raise HTTPException(status_code=404, detail="Risk profile not found")
    return profile
 
 
@router.patch("/{supplier_id}/risk-score", response_model=RiskProfileResponse)
async def update_risk_score_endpoint(
    supplier_id: UUID,
    request: RiskScoreUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    overall_risk_score 갱신 → risk_level 자동 재계산 → RiskProfileUpdated 발행.
    (커밋·발행은 risk_service가 처리. router에서 db.commit() 하지 않는다.)
    """
    profile = await service.upsert_risk_score(supplier_id, request.score, db)
    return profile