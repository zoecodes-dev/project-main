"""
domains/data_consent/router.py

제3자 정보제공 동의서 = 데이터 계약(Data Contract) 엔드포인트.
- POST   /data-consents               동의서 발송(계약 오퍼 생성)
- GET    /data-consents?supplier_id=  협력사 데이터 계약 이력 조회
- PATCH  /data-consents/{id}          회신/서명/철회(상태 전이 + 회신 양식 데이터 영속)
"""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from backend.domains.data_consent import service
from backend.domains.data_consent.models import (
    ConsentCreateBody, ConsentUpdateBody, ConsentResponse,
)
from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/data-consents", tags=["Data Consent (Data Contract)"])


@router.post("", response_model=ConsentResponse, status_code=201)
async def create_consent_endpoint(
    body: ConsentCreateBody,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """제3자 정보제공 동의서 발송 = 데이터 계약 오퍼 생성(status='requested')."""
    return await service.create_consent(
        db, tenant_id=current_user.tenant_id, requested_by=current_user.user_id, body=body,
    )


@router.get("", response_model=List[ConsentResponse])
async def list_consents_endpoint(
    supplier_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """협력사의 데이터 계약(동의서) 이력 조회. 내 테넌트 + 공용만."""
    return await service.list_consents(db, supplier_id, current_user.tenant_id)


@router.patch("/{consent_id}", response_model=ConsentResponse)
async def update_consent_endpoint(
    consent_id: uuid.UUID,
    body: ConsentUpdateBody,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """동의서 회신/서명/철회 — 상태 전이(returned/agreed/revoked) + 회신 양식 데이터 영속."""
    row = await service.update_consent(db, consent_id, body)
    if row is None:
        raise HTTPException(status_code=404, detail="Consent not found")
    return row
