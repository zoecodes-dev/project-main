"""
backend/domains/regulation/service.py  (담당: 팀원 C — 은지)

★ [B1] regulation 도메인 비즈니스 로직 계층

[Wave 0 → Wave 1 교체 이력]
  Wave 0: 더미 데이터 반환 스텁 (D 언블락용)
  Wave 1: 실제 DB 조회로 교체

[이 파일의 역할]
  규제 관련 비즈니스 로직을 담당한다.
  DB 접근은 모두 repository.py에 위임하고,
  이 파일은 비즈니스 규칙 적용 + 응답 변환만 수행한다.

[레이어 규칙]
  router.py → 여기(service.py) → repository.py → models.py  (단방향)
  - 직접 SQL 실행 금지 (repository 위임)
  - 타 도메인 import 금지 (이벤트로만 통신)

[제공하는 공개 함수 — 다른 모듈이 의존하는 계약]
  get_applicable_regulations(db, product_id)   → list[dict]
  get_regulations_by_destination(db, dest)     → list[dict]
  get_required_fields(db, regulation_code)     → list[dict]
  search_regulations(db, query, code, top_k)   → list[dict]

  ── [신규 §2.3a / §7.1 — 공유 서비스] ──
  list_violations(db, tenant_id, supplier_id, limit)  → list[dict]
  count_violations(db, tenant_id, supplier_id)         → int
  list_regulation_results(db, tenant_id, page, size)  → list[dict]
  count_regulation_results(db, tenant_id)              → int

[함수 시그니처 변경 안내]
  Wave 0 스텁 대비 db: AsyncSession 파라미터가 추가됐다.
  D(영수) C2 코드에서 호출 시 db 세션을 넘겨줘야 한다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.regulation import repository as reg_repo
from backend.domains.regulation.models import Regulation

logger = logging.getLogger(__name__)


# ============================================================
# 1. 제품에 적용되는 규제 목록 조회
# ============================================================

async def get_applicable_regulations(
    db: AsyncSession,
    product_id: str,
) -> list[dict[str, Any]]:
    """
    주어진 제품에 적용되는 규제 목록을 반환한다.

    [C-2 변경 — 은지, 2026-06-30] destination="EU" 하드코딩 → 실조회로 교체
      변경 전: destination = "EU" 고정 (데모 시나리오 기준)
      변경 후: product_id → batches.destination 실조회
               batches.destination ∈ US/EU/KR (BOTH 없음 — schema:chk_batch_destination)
               조회 결과 없으면 "EU" 폴백 유지 (기존 동작 보존)

    [무회귀]
      하위 get_regulations_by_destination() 시그니처·반환 형태 불변.
      REGULATION_BY_DESTINATION dict 불변.
    """
    from sqlalchemy import text as _text

    dest_row = (await db.execute(
        _text("""
            SELECT DISTINCT b.destination
            FROM batches b
            WHERE b.product_id = :product_id::uuid
              AND b.destination IS NOT NULL
            ORDER BY b.destination
            LIMIT 1
        """),
        {"product_id": product_id},
    )).fetchone()

    destination = dest_row[0] if dest_row else "EU"

    if not dest_row:
        logger.debug(
            "get_applicable_regulations: product_id=%s 에 해당하는 batch가 없어요. "
            "기본값 'EU' 사용.",
            product_id,
        )
    else:
        logger.debug(
            "get_applicable_regulations: product_id=%s → destination=%s (batches 실조회)",
            product_id, destination,
        )

    return await get_regulations_by_destination(db, destination)


# ============================================================
# 2. destination 기반 규제 목록 조회
# ============================================================

async def get_regulations_by_destination(
    db: AsyncSession,
    destination: str,
) -> list[dict[str, Any]]:
    """
    destination(출하 시장)에 적용되는 규제 목록을 DB에서 조회한다.
    """
    regulations: list[Regulation] = await reg_repo.get_by_destination(
        db, destination,
    )

    return [
        _regulation_to_dict(reg)
        for reg in regulations
    ]


# ============================================================
# 3. 규제별 필수 필드 조회
# ============================================================

async def get_required_fields(
    db: AsyncSession,
    regulation_code: str,
) -> list[dict[str, Any]]:
    """
    주어진 규제가 요구하는 필수 필드 목록을 반환한다.

    [현재 상태]
      D의 regulation_required_fields DDL 머지 전까지
      repository의 임시 더미 데이터를 반환한다.
    """
    return await reg_repo.get_required_fields(db, regulation_code)


# ============================================================
# 4. RAG 검색 (compliance.py에서 이관)
# ============================================================

async def search_regulations(
    db: AsyncSession,
    query_text: str,
    regulation_code: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """
    pgvector 코사인 유사도로 관련 규제 조항을 검색한다.
    """
    return await reg_repo.search_by_embedding(
        db, query_text, regulation_code, top_k,
    )


# ============================================================
# 5. 규제 단건 조회 (regulation_code 기준)
# ============================================================

async def get_regulation_by_code(
    db: AsyncSession,
    regulation_code: str,
) -> Optional[dict[str, Any]]:
    """
    regulation_code로 규제 단건을 조회한다.
    없으면 None.
    """
    reg = await reg_repo.get_by_code(db, regulation_code)
    if reg is None:
        return None
    return _regulation_to_dict(reg)


# ============================================================
# 7. [신규 §2.3a] 위반 목록 조회 — 공유 서비스 (1벌)
#
#    ★ 이 함수가 "단일 출처(Single Source of Truth)"다.
#       - GET /regulation/violations            (regulation router)
#       - GET /suppliers/{id}/violations §13.1  (supplier router — 위임받아 호출)
#    중복 쿼리 없이 supplier_id 파라미터 하나로 양쪽을 커버한다.
# ============================================================

async def list_violations(
    db: AsyncSession,
    tenant_id: UUID,
    supplier_id: Optional[UUID] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    compliance_results(verdict=compliance_violation) 위반 목록을 반환한다.

    [보안]
      repository 계층에서 batches JOIN으로 tenant 격리.
      이 함수는 tenant_id를 그대로 repository에 위임할 뿐이다.

    [파라미터]
      db          : AsyncSession
      tenant_id   : 현재 사용자 테넌트 (router에서 current_user.tenant_id 주입)
      supplier_id : 선택 필터. None이면 전체 테넌트, UUID 지정 시 해당 공급사만.
      limit       : 최대 반환 건수

    [§13.1 위임 예시 — supplier router에서]
      from backend.domains.regulation.service import list_violations, count_violations

      items = await list_violations(db, current_user.tenant_id, supplier_id=supplier_id)
      total = await count_violations(db, current_user.tenant_id, supplier_id=supplier_id)
    """
    return await reg_repo.get_violations(
        db,
        tenant_id=tenant_id,
        supplier_id=supplier_id,
        limit=limit,
    )


async def count_violations(
    db: AsyncSession,
    tenant_id: UUID,
    supplier_id: Optional[UUID] = None,
) -> int:
    """
    list_violations와 동일한 필터 기준 전체 건수를 반환한다.
    X-Total-Count 헤더 계산용.
    """
    return await reg_repo.count_violations(
        db,
        tenant_id=tenant_id,
        supplier_id=supplier_id,
    )


# ============================================================
# 8. [신규 §7.1] 규제 판정 전체 목록 조회
# ============================================================

async def list_regulation_results(
    db: AsyncSession,
    tenant_id: UUID,
    page: int = 1,
    size: int = 20,
) -> list[dict[str, Any]]:
    """
    /materials/regulation-results 응답 데이터를 반환한다.

    [HITL 후보]
      confidence < 0.85 이거나 compliance_results.needs_human_review = TRUE 인 경우
      needs_human_review = True로 응답.
      판단 로직은 repository에서 수행.
    """
    return await reg_repo.get_regulation_results(
        db,
        tenant_id=tenant_id,
        page=page,
        size=size,
    )


async def count_regulation_results(
    db: AsyncSession,
    tenant_id: UUID,
) -> int:
    """
    list_regulation_results 전체 건수. X-Total-Count 헤더용.
    """
    return await reg_repo.count_regulation_results(db, tenant_id=tenant_id)


# ============================================================
# 내부 헬퍼
# ============================================================

def _regulation_to_dict(reg: Regulation) -> dict[str, Any]:
    """
    Regulation ORM 객체를 dict로 변환한다.
    """
    return {
        "regulation_id":   str(reg.regulation_id),
        "regulation_code": reg.regulation_code,
        "name":            reg.name,
        "description":     reg.description,
        "destination":     reg.region,     # DB: region → API: destination
        "version":         reg.version,
        "effective_from":  (
            reg.effective_from.isoformat() if reg.effective_from else None
        ),
    }