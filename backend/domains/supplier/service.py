import dataclasses
from uuid import UUID
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.supplier import repository
from backend.domains.supplier.models import Supplier
from backend.events.types import SupplierInvitedEvent
from backend.infrastructure.event_bus import publish

async def create_supplier_and_invite(db: AsyncSession, supplier_data: dict, email: str) -> Supplier:
    # 1. DB에 협력사 생성
    supplier = await repository.create_supplier(db, supplier_data)
    
    # 2. SLA 마감일 계산 (현재 시간 기준 14일 후)
    sla_due_date = datetime.now(timezone.utc) + timedelta(days=14)
    
    # 3. 이벤트 객체 생성 및 발행 (팀 레퍼런스 파트)
    event = SupplierInvitedEvent(
        supplier_id=supplier.supplier_id,
        email=email,
        sla_due_date=sla_due_date
    )
    # 인프라 시그니처 계약 준수: db 객체 없이 2-인자 호출
    await publish("SupplierInvited", dataclasses.asdict(event))
    
    return supplier

async def get_supplier(db: AsyncSession, supplier_id: UUID) -> Optional[Supplier]:
    return await repository.get_supplier_by_id(db, supplier_id)

async def list_suppliers(db: AsyncSession, status: str = None, tier: int = None, risk_level: str = None) -> List[Supplier]:
    return await repository.get_suppliers(db, status, tier, risk_level)