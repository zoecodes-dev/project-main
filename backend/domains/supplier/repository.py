from uuid import UUID
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.domains.supplier.models import Supplier

async def create_supplier(db: AsyncSession, supplier_data: dict) -> Supplier:
    supplier = Supplier(**supplier_data)
    db.add(supplier)
    await db.flush()
    return supplier

async def get_supplier_by_id(db: AsyncSession, supplier_id: UUID) -> Optional[Supplier]:
    stmt = select(Supplier).where(Supplier.supplier_id == supplier_id).options(
        selectinload(Supplier.manufacturer_detail),
        selectinload(Supplier.recycler_detail),
        selectinload(Supplier.trader_detail),
        selectinload(Supplier.miner_detail),
        selectinload(Supplier.factories)
    )
    result = await db.execute(stmt)
    return result.scalars().first()

async def get_suppliers(db: AsyncSession, status: str = None, tier: int = None, risk_level: str = None) -> List[Supplier]:
    stmt = select(Supplier)
    if status:
        stmt = stmt.where(Supplier.status == status)
    if tier:
        stmt = stmt.where(Supplier.tier == tier)
    if risk_level:
        stmt = stmt.where(Supplier.risk_level == risk_level)
    
    result = await db.execute(stmt)
    return result.scalars().all()