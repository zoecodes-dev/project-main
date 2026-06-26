"""
domains/supplier/router.py  (담당: 팀원 B)

Supplier 도메인 HTTP 진입점(얇은 라우팅 레이어).
- 비즈니스 로직·커밋·이벤트 발행은 service가 담당. router는 요청 수신/응답만.
- 커밋은 service에서 일원화한다. ★ router에서 db.commit() 하지 않는다.
  (service.create_supplier_and_invite가 커밋 후 발행까지 책임진다)
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import get_db
from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.pagination import set_total_count
from backend.domains.supplier import service
# 스키마 클래스들을 models 내부 하단에서 안전하게 import
from backend.domains.supplier.models import (
    SupplierCreateRequest,
    SupplierBrief,
    SupplierDetailResponse,
    RiskProfileResponse,
    RiskScoreUpdateRequest,
    SupplierEsgResponse,
    SupplierTrainingResponse,
    SupplierReliabilityResponse,
    SupplierFactoriesResponse,
    MasterFormRequest,
    MasterFormResponse,
    MasterFormPrefillResponse,
)

router = APIRouter(prefix="/suppliers", tags=["Suppliers"])


async def authorized_supplier(
    supplier_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """
    소유권 게이트(§0.2). 경로의 supplier_id 가 토큰 테넌트 소유인지 확인.
    아니면(없거나 남의 테넌트) 404 — 존재 은닉(다른 테넌트 리소스의 존재를 숨김).
    하위 리소스 엔드포인트(esg/training/factories/reliability/risk-profile/master-form)가
    `Depends(authorized_supplier)` 로 재사용한다.
    """
    if not await service.supplier_in_tenant(db, supplier_id, current_user.tenant_id):
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier_id


@router.post("", status_code=201)
async def create_supplier_endpoint(
    request: SupplierCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """협력사 등록 및 초대 이벤트 발행. (커밋·발행은 service가 처리)"""
    # 테넌트는 토큰에서 강제(§0.2) — 클라이언트가 보낸 tenant_id 는 신뢰하지 않는다(교차생성 방지).
    supplier_data = {
        "tenant_id": current_user.tenant_id,
        "company_name": request.company_name,
        "provider_type": request.provider_type,
    }
    supplier = await service.create_supplier_and_invite(
        db, supplier_data, request.email, request.inviter_supplier_id
    )
    # ★ 여기서 db.commit() 하지 않는다 — service가 이미 커밋
    return {"supplier_id": supplier.supplier_id, "status": supplier.status}


@router.post("/{supplier_id}/master-form", response_model=MasterFormResponse)
async def submit_master_form_endpoint(
    supplier_id: UUID,
    form: MasterFormRequest,
    _auth: UUID = Depends(authorized_supplier),
    db: AsyncSession = Depends(get_db),
):
    """
    마스터폼(표준화된 단일 입력양식) 제출 — 섹션 0~6을 한 번에 받아 도메인별로 분배
    저장한다. service가 단일 트랜잭션으로 atomic commit(한 섹션 실패 시 전체 롤백).
    ★ router에서 db.commit() 하지 않는다 — service가 일원화.
    """
    result = await service.submit_master_form(db, supplier_id, form)
    if result is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return result


@router.get("/{supplier_id}/master-form/prefill", response_model=MasterFormPrefillResponse)
async def get_master_form_prefill_endpoint(
    supplier_id: UUID,
    _auth: UUID = Depends(authorized_supplier),
    db: AsyncSession = Depends(get_db),
):
    """
    AP(AI 자동 채움): 협력사가 업로드한 보완 문서의 AI 추출결과를 마스터폼 섹션 구조로
    모아 prefill 초안을 반환한다. 협력사는 이를 검토·정정 후 master-form으로 제출한다.
    추출결과가 없으면 빈 prefill(document_count=0)로 정상 반환(업로드 전 상태).
    """
    data = await service.get_master_form_prefill(db, supplier_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return data


@router.get("/{supplier_id}", response_model=SupplierBrief)
async def get_supplier_endpoint(
    supplier_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """협력사 단건 상세 조회. 내 테넌트 소유가 아니면 404(존재 은닉)."""
    supplier = await service.get_supplier(db, supplier_id, current_user.tenant_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier  # response_model(SupplierBrief)이 ORM→스키마 변환


@router.get("/{supplier_id}/detail", response_model=SupplierDetailResponse)
async def get_supplier_detail_endpoint(
    supplier_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    협력사 단건 + CTI 상세(provider type별) 조회.
    provider_type에 해당하는 detail 1종만 채워져 반환된다. 내 테넌트 소유만(아니면 404).
    """
    supplier = await service.get_supplier_detail(db, supplier_id, current_user.tenant_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier


@router.get("", response_model=List[SupplierBrief])
async def list_suppliers_endpoint(
    response: Response,
    status: Optional[str] = None,
    risk_level: Optional[str] = None,
    feoc_status: Optional[str] = None,
    page: int = 1,
    size: int = 20,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    협력사 목록 필터링 조회 (status / risk_level / feoc_status + 페이지). 내 테넌트만(§0.2).
    전체 건수는 X-Total-Count 헤더로 전달(§0.6) — 본문은 bare array 유지.
    """
    items = await service.list_suppliers(
        db, status, risk_level, feoc_status, page, size, current_user.tenant_id
    )
    total = await service.count_suppliers(
        db, status, risk_level, feoc_status, current_user.tenant_id
    )
    set_total_count(response, total)
    return items
 
@router.get("/{supplier_id}/risk-profile", response_model=RiskProfileResponse)
async def get_risk_profile_endpoint(
    supplier_id: UUID,
    _auth: UUID = Depends(authorized_supplier),
    db: AsyncSession = Depends(get_db),
):
    """협력사 리스크 프로필 조회. 내 테넌트 소유만(아니면 404)."""
    profile = await service.get_risk_profile(supplier_id, db)
    if not profile:
        raise HTTPException(status_code=404, detail="Risk profile not found")
    return profile
 
 
@router.patch("/{supplier_id}/risk-score", response_model=RiskProfileResponse)
async def update_risk_score_endpoint(
    supplier_id: UUID,
    request: RiskScoreUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    overall_risk_score 갱신 → risk_level 자동 재계산 → RiskProfileUpdated 발행.
    (커밋·발행은 risk_service가 처리. router에서 db.commit() 하지 않는다.)
    """
    # 입력 검증: 점수는 0~100 범위 (범위 밖이면 422)
    if not (0 <= request.score <= 100):
        raise HTTPException(
            status_code=422, detail="score must be between 0 and 100"
        )
    # 존재하지 않거나 내 테넌트 소유가 아니면 404 (교차 테넌트 수정·프로필 생성 방지)
    if not await service.get_supplier(db, supplier_id, current_user.tenant_id):
        raise HTTPException(status_code=404, detail="Supplier not found")

    profile = await service.upsert_risk_score(supplier_id, request.score, db)
    return profile


# ============================================================
# BE-3: 7탭 모달 조회 엔드포인트 (기존 테이블 SELECT 전용)
# ============================================================
@router.get("/{supplier_id}/esg", response_model=SupplierEsgResponse)
async def get_supplier_esg_endpoint(
    supplier_id: UUID,
    _auth: UUID = Depends(authorized_supplier),
    db: AsyncSession = Depends(get_db),
):
    """ESG 탭 — 인증서(E) + 인권 이슈/산업재해(S) + 실사 기록(G) 조회. 내 테넌트 소유만."""
    data = await service.get_esg(db, supplier_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return data


@router.get("/{supplier_id}/training", response_model=SupplierTrainingResponse)
async def get_supplier_training_endpoint(
    supplier_id: UUID,
    _auth: UUID = Depends(authorized_supplier),
    db: AsyncSession = Depends(get_db),
):
    """Training 탭 — 교육 이수 기록(교육 자료 메타 포함) 조회. 내 테넌트 소유만."""
    data = await service.get_training(db, supplier_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return data


@router.get("/{supplier_id}/reliability", response_model=SupplierReliabilityResponse)
async def get_supplier_reliability_endpoint(
    supplier_id: UUID,
    _auth: UUID = Depends(authorized_supplier),
    db: AsyncSession = Depends(get_db),
):
    """Reliability(신뢰도) 탭 — 완성도 + 리스크 프로필 + 온보딩 SLA + 실사 요약 조회. 내 테넌트 소유만."""
    data = await service.get_reliability(db, supplier_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return data


@router.get("/{supplier_id}/factories", response_model=SupplierFactoriesResponse)
async def get_supplier_factories_endpoint(
    supplier_id: UUID,
    _auth: UUID = Depends(authorized_supplier),
    db: AsyncSession = Depends(get_db),
):
    """사업장 탭 — 공장/광산 목록(PostGIS 좌표 lat/lng 포함) 조회. 내 테넌트 소유만."""
    data = await service.get_factories(db, supplier_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return data