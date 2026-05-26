from uuid import UUID
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.infrastructure.database import get_db
from backend.domains.supplier import service

router = APIRouter(prefix="/suppliers", tags=["Suppliers"])

class SupplierCreateRequest(BaseModel):
    tenant_id: UUID
    company_name: str
    supplier_type: str
    email: str

@router.post("", status_code=201)
async def create_supplier_endpoint(request: SupplierCreateRequest, db: AsyncSession = Depends(get_db)):
    """협력사 등록 및 초대 이벤트 발행"""
    supplier_data = {
        "tenant_id": request.tenant_id,
        "company_name": request.company_name,
        "supplier_type": request.supplier_type,
    }
    supplier = await service.create_supplier_and_invite(db, supplier_data, request.email)
    await db.commit()
    
    return {"supplier_id": supplier.supplier_id, "status": supplier.status}

@router.get("/{supplier_id}")
async def get_supplier_endpoint(supplier_id: UUID, db: AsyncSession = Depends(get_db)):
    """협력사 단건 상세 조회 (CTI 포함)"""
    supplier = await service.get_supplier(db, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier

@router.get("")
async def list_suppliers_endpoint(
    status: Optional[str] = None, 
    tier: Optional[int] = None, 
    risk_level: Optional[str] = None, 
    db: AsyncSession = Depends(get_db)
):
    """협력사 목록 필터링 조회"""
    return await service.list_suppliers(db, status, tier, risk_level)