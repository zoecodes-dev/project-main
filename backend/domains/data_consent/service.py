"""
domains/data_consent/service.py

제3자 정보제공 동의서(데이터 계약) 비즈니스 로직. 커밋 일원화(service).
"""
import uuid
from typing import Optional, List, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.data_consent import repository


async def create_consent(db: AsyncSession, *, tenant_id, requested_by, body) -> Dict[str, Any]:
    """동의서 발송(데이터 계약 오퍼) 생성 — status='requested'."""
    row = await repository.create(db, tenant_id=tenant_id, requested_by=requested_by, body=body)
    await db.commit()
    return row


async def list_consents(db: AsyncSession, supplier_id: uuid.UUID, tenant_id: Optional[uuid.UUID]) -> List[Dict[str, Any]]:
    """협력사의 데이터 계약 이력 조회(최신순)."""
    return await repository.list_by_supplier(db, supplier_id, tenant_id)


async def update_consent(db: AsyncSession, consent_id: uuid.UUID, body) -> Optional[Dict[str, Any]]:
    """회신/서명/철회 — 상태 전이 + 회신 양식 데이터 영속."""
    row = await repository.update_status(db, consent_id, body)
    if row is None:
        return None
    await db.commit()
    return row
