"""
backend/domains/regulation/router.py  (담당: 팀원 C — 은지)

★ [B1] regulation 도메인 REST API 엔드포인트

[이 파일의 역할]
  규제 관련 HTTP 엔드포인트를 정의한다.
  비즈니스 로직은 service.py에 위임하고,
  이 파일은 HTTP 요청 파싱 + 응답 직렬화만 담당한다.

[엔드포인트 목록]
  GET  /regulations                       전체 규제 목록 (destination 필터)
  GET  /regulations/{code}                규제 단건 조회 (regulation_code)
  GET  /regulations/applicable            제품에 적용되는 규제 목록
  GET  /regulations/{code}/required-fields 규제별 필수 필드 목록

[레이어 규칙]
  여기(router.py) → service.py → repository.py → models.py  (단방향)
  - router는 db.commit() 하지 않는다.
  - router는 비즈니스 로직을 직접 구현하지 않는다.

[FastAPI Depends 패턴 설명 (초보자용)]
  FastAPI의 Depends()는 의존성 주입(Dependency Injection) 도구다.
  예: db: AsyncSession = Depends(get_db)
  → FastAPI가 요청마다 자동으로 DB 세션을 생성해서 db에 넣어주고,
    응답 후 자동으로 세션을 닫는다.
  → 개발자는 db 세션 열고/닫기를 직접 관리할 필요가 없다.

[main.py 등록 방법]
  from backend.domains.regulation.router import router as regulation_router
  app.include_router(regulation_router)
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.regulation import service as reg_service
from backend.domains.regulation.models import RegulationResponse, RequiredFieldResponse
from backend.infrastructure.database import get_db

# ── 라우터 생성 ──
# prefix: 이 라우터의 모든 경로 앞에 /regulations가 자동 추가됨
# tags: Swagger UI에서 그룹핑 이름
router = APIRouter(
    prefix="/regulations",
    tags=["regulations"],
)


# ============================================================
# 1. GET /regulations — 전체 규제 목록
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

    [사용 예시]
      GET /regulations              → 전체 10건
      GET /regulations?destination=EU → EU 규제 8건
    """
    if destination:
        # destination 필터가 있으면 해당 시장 규제만 조회
        regs = await reg_service.get_regulations_by_destination(db, destination)
    else:
        # 필터 없으면 전체 조회 (repository.get_all 직접 호출)
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
# 2. GET /regulations/applicable — 제품에 적용되는 규제
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

    [동작]
      product_id → destination 조회 → 해당 시장 규제 목록 반환.
      (현재 destination은 기본값 'EU'. A1 머지 후 실제 조회로 교체.)

    [사용 예시]
      GET /regulations/applicable?product_id=xxxx-xxxx
    """
    return await reg_service.get_applicable_regulations(db, product_id)


# ============================================================
# 3. GET /regulations/{code} — 규제 단건 조회
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

    [경로 파라미터]
      code : 'EU_BATTERY', 'UFLPA' 등

    [에러]
      404 : 해당 regulation_code가 존재하지 않는 경우.

    [사용 예시]
      GET /regulations/EU_BATTERY
    """
    result = await reg_service.get_regulation_by_code(db, code)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"규제 코드 '{code}'를 찾을 수 없습니다.",
        )

    return result


# ============================================================
# 4. GET /regulations/{code}/required-fields — 규제별 필수 필드
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

    [현재 상태]
      D의 regulation_required_fields DDL 머지 전까지 더미 데이터 반환.

    [경로 파라미터]
      code : 'EU_BATTERY', 'UFLPA' 등

    [사용 예시]
      GET /regulations/EU_BATTERY/required-fields
    """
    return await reg_service.get_required_fields(db, code)
