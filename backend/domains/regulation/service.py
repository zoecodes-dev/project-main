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
  save_origin_certificates(db, sid, certs)     → list[str]

[함수 시그니처 변경 안내]
  Wave 0 스텁 대비 db: AsyncSession 파라미터가 추가됐다.
  D(영수) C2 코드에서 호출 시 db 세션을 넘겨줘야 한다.
  이것은 Wave 0 docstring에 예고된 변경이다.
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

    [Wave 0 스텁과의 차이]
      스텁: product_id 무시, 더미 데이터 고정 반환
      현재: product_id로 destination 조회 후 DB에서 실제 규제 조회

    [동작 흐름]
      1. product_id → batches 테이블에서 destination 조회
         (현재는 TODO — A1 머지 후 배치 진입점으로 연동)
      2. destination → get_regulations_by_destination() 호출
      3. Regulation ORM 리스트 → dict 리스트로 변환

    [파라미터]
      db         : SQLAlchemy 비동기 세션
      product_id : 제품 UUID 문자열

    [반환]
      규제 정보 dict 리스트. 각 항목:
        regulation_id, regulation_code, name, description,
        region, version, effective_from

    [사용 예시 — D(영수) C2 맵 gap 계산]
      from backend.domains.regulation.service import get_applicable_regulations

      regulations = await get_applicable_regulations(db, product_id=str(product_id))
      for reg in regulations:
          fields = await get_required_fields(db, reg["regulation_code"])
    """
    # ── TODO: product_id → destination 조회 ──
    # A1(배치 생성 진입점) 머지 후 아래 로직 활성화:
    #   from sqlalchemy import text
    #   dest_row = await db.execute(
    #       text("""
    #           SELECT DISTINCT b.destination
    #           FROM batches b
    #           WHERE b.product_id = :product_id::uuid
    #           ORDER BY b.destination
    #           LIMIT 1
    #       """),
    #       {"product_id": product_id},
    #   )
    #   destination = dest_row.scalar() or "EU"
    #
    # 현재는 기본값 "EU" 사용 (데모 시나리오 기준)
    destination = "EU"

    logger.debug(
        "get_applicable_regulations: product_id=%s → destination=%s",
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

    [기존 compliance.py REGULATION_BY_DESTINATION 하드코딩 대체]
      기존: 파이썬 딕셔너리에 규제 코드 목록을 하드코딩
      신규: regulations.region 컬럼 기반 DB 쿼리
            규제가 추가되면 시드 데이터만 INSERT하면 됨

    [파라미터]
      destination : 'EU' / 'US' / 'BOTH' / 'KR'

    [반환]
      규제 정보 dict 리스트. compliance.py에서 사용하는
      "destination" 키는 DB의 region 컬럼 값으로 채운다.
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

    [D 머지 후]
      repository.get_required_fields()가 DB에서 실제 조회.
      이 service 함수는 수정 불필요 (repository만 교체).

    [파라미터]
      regulation_code : 'EU_BATTERY', 'UFLPA' 등

    [반환]
      필수 필드 dict 리스트. 각 항목:
        field_name, field_type, is_mandatory, provider_type_applicable
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

    [기존 위치] compliance.py search_regulations()
    [이관 후]   regulation 도메인 service → repository 위임

    compliance.py의 judge 함수들이 이 함수를 호출한다:
      from backend.domains.regulation.service import search_regulations
      clauses = await search_regulations(db, "FEOC ownership ...", "IRA", top_k=5)

    [파라미터]
      query_text      : 검색 쿼리 (자연어)
      regulation_code : 검색 범위 한정 규제 코드
      top_k           : 반환할 최대 결과 수

    [반환]
      규제 조항 dict 리스트 (similarity 점수 포함)
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

    [파라미터]
      regulation_code : 'EU_BATTERY' 등

    [반환]
      규제 정보 dict. 없으면 None.
    """
    reg = await reg_repo.get_by_code(db, regulation_code)
    if reg is None:
        return None
    return _regulation_to_dict(reg)


# ============================================================
# 6. 마스터폼 섹션 3 — 원산지 인증서 저장
# ============================================================

async def save_origin_certificates(
    db: AsyncSession,
    supplier_id: str,
    certificates: list[dict[str, Any]],
) -> list[str]:
    """
    마스터폼 섹션 3 원산지 인증서를 origin_certificates 테이블에 저장한다.

    [호출 구조]
      B의 supplier/service.py (마스터폼 진입점)
        → 이 함수 호출
          → repository.write_origin_certificates() 위임
      commit은 B의 service가 일괄 수행 (atomic 보장).

    [파라미터]
      db            : SQLAlchemy 비동기 세션
      supplier_id   : 협력사 UUID 문자열
      certificates  : MasterFormOriginCert.model_dump() 리스트

    [반환]
      생성된 cert_id 문자열 리스트

    [사용 예시 — B의 service에서]
      from backend.domains.regulation.service import save_origin_certificates

      if form.origin and form.origin.origin_certificates:
          cert_ids = await save_origin_certificates(
              db=db,
              supplier_id=str(supplier_id),
              certificates=[c.model_dump() for c in form.origin.origin_certificates],
          )
          sections_saved.append("origin_certificates")
    """
    if not certificates:
        logger.debug("save_origin_certificates: 저장할 인증서가 없습니다.")
        return []

    return await reg_repo.write_origin_certificates(
        db, supplier_id, certificates,
    )


# ============================================================
# 내부 헬퍼
# ============================================================

def _regulation_to_dict(reg: Regulation) -> dict[str, Any]:
    """
    Regulation ORM 객체를 dict로 변환한다.

    [키 이름 설계]
      - regulation_id  : str (UUID → 문자열 변환)
      - destination    : DB 컬럼명은 region이지만, 호출자 관점에서
                         '적용 시장'이라는 의미의 destination으로 노출.
                         compliance.py REGULATION_BY_DESTINATION 키와 일치.
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
