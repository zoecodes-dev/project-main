"""
backend/domains/regulation/router.py  (담당: 팀원 C — 은지)

★ [B1] regulation 도메인 REST API 엔드포인트

[이 파일의 역할]
  규제 관련 HTTP 엔드포인트를 정의한다.
  비즈니스 로직은 service.py에 위임하고,
  이 파일은 HTTP 요청 파싱 + 응답 직렬화만 담당한다.

[엔드포인트 목록]
  ── 기존 (규제 마스터 데이터) ──
  GET  /regulations                       전체 규제 목록 (destination 필터)
  GET  /regulations/{code}                규제 단건 조회 (regulation_code)
  GET  /regulations/applicable            제품에 적용되는 규제 목록
  GET  /regulations/{code}/required-fields 규제별 필수 필드 목록

  ── 신규 (compliance_results 판정/위반 데이터) prefix: /regulation ──
  GET  /regulation/violations             위반 목록 §2.3a
  GET  /regulation/materials/regulation-results  규제 판정 목록 §7.1

[레이어 규칙]
  여기(router.py) → service.py → repository.py → models.py  (단방향)
  - router는 db.commit() 하지 않는다.
  - router는 비즈니스 로직을 직접 구현하지 않는다.

[main.py 등록 방법]
  from backend.domains.regulation.router import router as regulation_router
  from backend.domains.regulation.router import compliance_router
  app.include_router(regulation_router)
  app.include_router(compliance_router)
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.regulation import service as reg_service
from backend.domains.regulation.models import RegulationResponse, RequiredFieldResponse
from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db
from backend.infrastructure.pagination import set_total_count

# ── 기존 라우터 (규제 마스터 데이터) ──
router = APIRouter(
    prefix="/regulations",
    tags=["regulations"],
)

# ── 신규 라우터 (compliance_results 판정/위반) ──
# prefix를 /regulation(단수)으로 분리해 기존 /regulations와 충돌 없이 공존
compliance_router = APIRouter(
    prefix="/regulation",
    tags=["Regulation — Compliance Results"],
)


# ============================================================
# [기존] 1. GET /regulations — 전체 규제 목록
# ============================================================

@router.get(
    "",
    response_model=list[RegulationResponse],
    summary="규제 전체 목록 조회",
)
async def list_regulations(
    destination: Optional[str] = Query(
        None,
        description="적용 시장 필터 (EU / US / BOTH). 생략 시 전체 반환.",
        regex="^(EU|US|BOTH)$",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    등록된 규제 전체 목록을 반환한다.

    [쿼리 파라미터]
      destination (선택): 'EU', 'US', 'BOTH' 중 하나.
                          지정하면 해당 시장에 적용되는 규제만 반환.
    """
    if destination:
        regs = await reg_service.get_regulations_by_destination(db, destination)
    else:
        from backend.domains.regulation import repository as reg_repo
        orm_list = await reg_repo.get_all(db)
        regs = [
            {
                "regulation_id":   str(r.regulation_id),
                "regulation_code": r.regulation_code,
                "name":            r.name,
                "region":          r.region,
                "description":     r.description,
                "version":         r.version,
                "effective_from":  r.effective_from,
                "embedding_status": r.embedding_status,
            }
            for r in orm_list
        ]

    return regs


# ============================================================
# [기존] 2. GET /regulations/applicable — 제품에 적용되는 규제
# ============================================================

@router.get(
    "/applicable",
    summary="제품에 적용되는 규제 목록",
)
async def get_applicable_regulations(
    product_id: str = Query(
        ...,
        description="제품 UUID",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    특정 제품에 적용되는 규제 목록을 반환한다.
    """
    return await reg_service.get_applicable_regulations(db, product_id)


# ============================================================
# [기존] 3. GET /regulations/{code} — 규제 단건 조회
# ============================================================

@router.get(
    "/{code}",
    response_model=RegulationResponse,
    summary="규제 단건 조회 (regulation_code)",
)
async def get_regulation_by_code(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """
    regulation_code로 규제 단건을 조회한다.
    없으면 404.
    """
    result = await reg_service.get_regulation_by_code(db, code)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"규제 코드 '{code}'를 찾을 수 없습니다.",
        )

    return result


# ============================================================
# [기존] 4. GET /regulations/{code}/required-fields
# ============================================================

@router.get(
    "/{code}/required-fields",
    response_model=list[RequiredFieldResponse],
    summary="규제별 필수 필드 목록",
)
async def get_required_fields(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """
    특정 규제가 요구하는 필수 필드 목록을 반환한다.
    D의 regulation_required_fields DDL 머지 전까지 더미 데이터 반환.
    """
    return await reg_service.get_required_fields(db, code)


# ============================================================
# [신규 §2.3a] GET /regulation/violations
# ============================================================

@compliance_router.get(
    "/violations",
    summary="위반(violation) 목록 조회 §2.3a",
)
async def list_violations(
    response: Response,
    limit: int = Query(50, ge=1, le=200, description="최대 반환 건수"),
    supplier_id: Optional[UUID] = Query(None, description="공급사 UUID 필터 (선택)"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    verdict = 'compliance_violation' 인 위반 건 목록을 반환한다.

    [보안]
      - get_current_user: 미인증 요청 차단 (401)
      - tenant 격리: batches JOIN으로 현재 테넌트 데이터만 노출
        (compliance_results에 tenant_id 없음 → batches.tenant_id 필터)

    [응답]
      bare array (envelope 없음). 빈 결과 = [].
      X-Total-Count 헤더 포함.

    [§13.1 위임]
      supplier router의 GET /suppliers/{id}/violations 는
      reg_service.list_violations(db, tenant_id, supplier_id=id) 를 그대로 호출하면 된다.
    """
    items = await reg_service.list_violations(
        db,
        tenant_id=current_user.tenant_id,
        supplier_id=supplier_id,
        limit=limit,
    )
    total = await reg_service.count_violations(
        db,
        tenant_id=current_user.tenant_id,
        supplier_id=supplier_id,
    )
    set_total_count(response, total)
    return items  # bare array


# ============================================================
# [신규 §7.1] GET /regulation/materials/regulation-results
# ============================================================

@compliance_router.get(
    "/materials/regulation-results",
    summary="규제 판정 전체 목록 조회 §7.1",
)
async def list_regulation_results(
    response: Response,
    page: int = Query(1, ge=1, description="페이지 번호 (1-base)"),
    size: int = Query(20, ge=1, le=100, description="페이지 크기"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    compliance_results 전체 조회. HITL 후보(confidence < 0.85) 포함.

    [보안]
      - get_current_user: 미인증 요청 차단 (401)
      - tenant 격리: batches INNER JOIN (get_violations와 동일 패턴)

    [응답 필드]
      result_id, material, supplier_id, supplier_name, regulation,
      verdict(passed/violation/warning/reject), confidence,
      needs_human_review(bool), evidence(배열)

    [응답]
      bare array. 빈 결과 = []. X-Total-Count 헤더 포함.
    """
    items = await reg_service.list_regulation_results(
        db,
        tenant_id=current_user.tenant_id,
        page=page,
        size=size,
    )
    total = await reg_service.count_regulation_results(
        db,
        tenant_id=current_user.tenant_id,
    )
    set_total_count(response, total)
    return items  # bare array