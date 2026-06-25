"""
backend/domains/regulation/repository.py  (담당: 팀원 C — 은지)

★ [B1 + MF 섹션3] regulation 도메인 데이터 접근 계층

[이 파일의 역할]
  regulations 테이블에 대한 모든 DB 접근을 담당한다.
  직접 SQL은 여기서만 실행하고, service.py는 이 repository를 호출한다.
  (레이어 규칙: router → service → repository → models, 단방향)

[주요 함수 목록]
  ── 조회 ──
  get_all()              : 전체 규제 목록 (region 필터 지원)
  get_by_code()          : regulation_code로 단건 조회
  get_by_destination()   : destination(시장)에 적용되는 규제 목록 조회
  search_by_embedding()  : pgvector 코사인 유사도 RAG 검색
                           (기존 compliance.py search_regulations에서 이관)

  ── 쓰기 ──
  write_origin_certificates() : 마스터폼 섹션 3 원산지 인증서 INSERT
                                (B의 service가 atomic 트랜잭션에서 호출)

  ── [TODO: D 머지 후] ──
  get_required_fields()  : regulation_required_fields 테이블에서 조회

[중요 규칙]
  - 이 파일의 모든 함수는 db.commit()을 호출하지 않는다.
    commit은 호출자(service)가 담당한다.
  - write 함수는 flush()까지만 수행한다.
    (마스터폼은 B의 service에서 단일 트랜잭션 commit)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.regulation.models import Regulation

logger = logging.getLogger(__name__)


# ============================================================
# 1. 조회 — 기본 CRUD
# ============================================================

async def get_all(
    db: AsyncSession,
    region: Optional[str] = None,
) -> list[Regulation]:
    """
    규제 전체 목록을 반환한다. region 파라미터로 필터 가능.

    [파라미터]
      db     : SQLAlchemy 비동기 세션 (FastAPI Depends로 주입)
      region : 'EU' / 'US' / 'BOTH' 중 하나. None이면 전체 반환.

    [반환]
      Regulation ORM 객체 리스트.

    [사용 예시]
      eu_regs = await get_all(db, region="EU")
    """
    # ── SQLAlchemy select 구문 구성 ──
    # select(Regulation): "SELECT * FROM regulations" 와 동일.
    # .where()로 조건 추가, .order_by()로 정렬.
    stmt = select(Regulation).order_by(Regulation.regulation_code)

    if region is not None:
        stmt = stmt.where(Regulation.region == region)

    result = await db.execute(stmt)

    # .scalars(): Row 객체에서 ORM 인스턴스만 추출.
    # .all(): 리스트로 변환.
    return list(result.scalars().all())


async def get_by_code(
    db: AsyncSession,
    regulation_code: str,
) -> Optional[Regulation]:
    """
    regulation_code로 규제 단건을 조회한다.

    [파라미터]
      regulation_code : 'EU_BATTERY', 'UFLPA' 등 (schema.sql UNIQUE)

    [반환]
      Regulation ORM 객체. 없으면 None.

    [사용 예시]
      reg = await get_by_code(db, "EU_BATTERY")
      if reg is None:
          raise HTTPException(404, "규제를 찾을 수 없습니다.")
    """
    stmt = select(Regulation).where(
        Regulation.regulation_code == regulation_code
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_by_destination(
    db: AsyncSession,
    destination: str,
) -> list[Regulation]:
    """
    destination(출하 시장)에 적용되는 규제 목록을 조회한다.

    [기존 compliance.py REGULATION_BY_DESTINATION 매핑을 DB 쿼리로 대체]
      기존: 파이썬 딕셔너리에 하드코딩 → 규제 추가 시 코드 수정 필요
      신규: regulations.region 컬럼 기반 DB 쿼리 → 시드 데이터만 추가하면 됨

    [destination → region 매핑 규칙]
      "EU"   → region = 'EU'  인 규제
      "US"   → region = 'US'  인 규제
      "BOTH" → region IN ('EU', 'US', 'BOTH') — 합집합
      "KR"   → 빈 리스트 (국내 출하는 글로벌 규제 검사 대상 없음)

    [파라미터]
      destination : batches.destination 값 ('EU' / 'US' / 'BOTH' / 'KR')

    [반환]
      Regulation ORM 리스트. KR이면 빈 리스트.
    """
    # KR(국내 출하)은 글로벌 규제 검사 대상 없음 — 즉시 빈 리스트
    if destination == "KR":
        return []

    stmt = select(Regulation).order_by(Regulation.regulation_code)

    if destination == "BOTH":
        # EU + US + BOTH 합집합 (CONFLICT_MINERALS 같은 양쪽 적용 규제 포함)
        stmt = stmt.where(Regulation.region.in_(["EU", "US", "BOTH"]))
    else:
        # EU 또는 US 단일 시장
        stmt = stmt.where(Regulation.region == destination)

    result = await db.execute(stmt)
    return list(result.scalars().all())


# ============================================================
# 2. 조회 — pgvector RAG 검색
#    (기존 compliance.py search_regulations에서 이관)
# ============================================================

async def search_by_embedding(
    db: AsyncSession,
    query_text: str,
    regulation_code: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """
    pgvector 코사인 유사도로 관련 규제 조항을 검색한다.

    [기존 위치] compliance.py search_regulations()
    [이관 이유] 규제 데이터 접근은 regulation 도메인 repository의 책임.
               compliance.py는 이 함수를 호출하기만 한다.

    [동작 흐름]
      1. query_text를 Bedrock Cohere Embed v4 벡터로 변환
         (embedding_factory.embed_query 호출)
      2. regulations 테이블에서 regulation_code 필터 +
         embedding_status='indexed' 조건으로
         코사인 거리(<=> 연산자)가 가장 작은 row top_k개 반환
      3. 각 row를 dict로 변환

    [파라미터]
      query_text      : 검색할 자연어 쿼리 (예: "carbon footprint declaration")
      regulation_code : 검색 범위를 한정할 규제 코드 (예: "EU_BATTERY_ART7")
      top_k           : 반환할 최대 결과 수 (기본 3)

    [반환]
      규제 조항 dict 리스트. 각 항목에 similarity 점수 포함.

    [주의]
      - embedding_status='indexed' 인 row만 검색 대상.
        seed_regulation_embeddings.py를 먼저 실행해야 한다.
      - idx_regulations_embedding (hnsw, vector_cosine_ops) 인덱스가
        schema.sql에 정의돼 있어 대용량에도 빠르게 동작한다.
    """
    # ──────────────────────────────────────────────────────────────
    # [②-2 lazy import] embed_query를 함수 안에서만 import
    #
    #   변경 전 (모듈 레벨 import — 문제):
    #     파일 상단에 `from backend.llm.embedding_factory import embed_query`
    #     → regulation 도메인이 로드될 때마다 langchain_aws(Bedrock)를 끌어옴
    #     → /supply-chain/gaps 같은 Bedrock와 무관한 엔드포인트도
    #       langchain_aws 초기화를 시도 → AWS 없는 로컬에서 타임아웃/행
    #
    #   변경 후 (lazy import — 해결):
    #     embed_query가 실제로 필요한 이 함수 안에서만 import
    #     → get_by_destination(), get_required_fields() 등 DB 전용 함수는
    #       langchain_aws를 전혀 건드리지 않음
    #     → /supply-chain/gaps 정상 응답 (200)
    # ──────────────────────────────────────────────────────────────
    from backend.llm.embedding_factory import embed_query  # noqa: PLC0415

    # 1단계: 쿼리 텍스트를 벡터로 변환
    # embed_query()는 embedding_factory.py의 동기 함수 (Bedrock 호출)
    query_vector: list[float] = embed_query(query_text)

    # 2단계: pgvector 코사인 유사도 검색
    # <=> 연산자: 코사인 거리 (0에 가까울수록 유사)
    # 1.0 - distance = similarity (1에 가까울수록 유사)
    sql = text("""
        SELECT
            regulation_id::text,
            regulation_code,
            name,
            description,
            1.0 - (embedding <=> :query_vector::vector) AS similarity
        FROM regulations
        WHERE
            regulation_code  = :regulation_code
            AND embedding_status = 'indexed'
            AND embedding IS NOT NULL
        ORDER BY embedding <=> :query_vector::vector
        LIMIT :top_k
    """)

    rows = (
        await db.execute(
            sql,
            {
                "query_vector":    str(query_vector),
                "regulation_code": regulation_code,
                "top_k":           top_k,
            },
        )
    ).fetchall()

    return [
        {
            "regulation_id":   row.regulation_id,
            "regulation_code": row.regulation_code,
            "name":            row.name,
            "description":     row.description,
            "similarity":      float(row.similarity),
        }
        for row in rows
    ]


# ============================================================
# 3. [TODO] regulation_required_fields 조회
#    D(영수) C1 DDL 머지 후 구현
# ============================================================

# ┌──────────────────────────────────────────────────────────────┐
# │ D 머지 후 체크리스트:                                        │
# │   1. models.py의 RegulationRequiredField ORM 주석 해제        │
# │   2. 아래 함수를 실제 DB 쿼리로 교체                          │
# │   3. service.py의 get_required_fields()에서 이 함수 호출       │
# └──────────────────────────────────────────────────────────────┘

# ── 임시 더미 데이터 (D 머지 전까지만 사용) ──
# compliance.py _build_judge_context() 키 이름과 일치시킴
_TEMP_REQUIRED_FIELDS: dict[str, list[dict[str, Any]]] = {
    "EU_BATTERY": [
        {"field_name": "recycled_content_ratio", "field_type": "number",
         "is_mandatory": True, "provider_type_applicable": ["recycler", "manufacturer"]},
        {"field_name": "recycled_materials", "field_type": "jsonb",
         "is_mandatory": True, "provider_type_applicable": ["recycler"]},
    ],
    "EU_BATTERY_ART7": [
        {"field_name": "carbon_intensity", "field_type": "number",
         "is_mandatory": True, "provider_type_applicable": ["manufacturer"]},
        {"field_name": "factory_carbon_declarations", "field_type": "jsonb",
         "is_mandatory": True, "provider_type_applicable": ["manufacturer"]},
    ],
    "EUDR": [
        {"field_name": "mine_coordinates", "field_type": "string",
         "is_mandatory": True, "provider_type_applicable": ["miner"]},
        {"field_name": "origin_country", "field_type": "string",
         "is_mandatory": True, "provider_type_applicable": ["miner", "trader"]},
    ],
    "UFLPA": [
        {"field_name": "origin_country", "field_type": "string",
         "is_mandatory": True, "provider_type_applicable": ["miner", "trader"]},
        {"field_name": "geo_risk_flags", "field_type": "jsonb",
         "is_mandatory": False, "provider_type_applicable": ["miner"]},
    ],
    "IRA": [
        {"field_name": "feoc_direct_ownership", "field_type": "number",
         "is_mandatory": True, "provider_type_applicable": ["trader", "manufacturer"]},
        {"field_name": "feoc_indirect_ownership", "field_type": "number",
         "is_mandatory": False, "provider_type_applicable": ["trader", "manufacturer"]},
    ],
}


async def get_required_fields(
    db: AsyncSession,
    regulation_code: str,
) -> list[dict[str, Any]]:
    """
    [TODO — D 머지 후 DB 쿼리로 교체]
    현재는 더미 데이터 반환. D의 C1(regulation_required_fields DDL + 시드) 완료 후
    아래 주석 처리된 실제 DB 쿼리로 교체한다.

    [D 머지 후 교체할 코드]
    ───────────────────────────────
    # from backend.domains.regulation.models import RegulationRequiredField
    #
    # stmt = (
    #     select(RegulationRequiredField)
    #     .join(Regulation)
    #     .where(Regulation.regulation_code == regulation_code)
    # )
    # result = await db.execute(stmt)
    # rows = result.scalars().all()
    # return [
    #     {
    #         "field_name":               row.field_name,
    #         "field_type":               row.field_type,
    #         "is_mandatory":             True,  # 테이블 구조에 따라 조정
    #         "provider_type_applicable": row.provider_type_applicable or [],
    #     }
    #     for row in rows
    # ]
    ───────────────────────────────
    """
    fields = _TEMP_REQUIRED_FIELDS.get(regulation_code, [])

    if not fields:
        logger.warning(
            "[TODO] get_required_fields: regulation_code=%s 매핑 없음. "
            "D의 C1 머지 후 DB 쿼리로 교체 필요.",
            regulation_code,
        )

    return fields


# ============================================================
# 4. 쓰기 — 마스터폼 섹션 3 원산지 인증서 INSERT
#    B(은진)의 service가 atomic 트랜잭션에서 호출
# ============================================================

async def write_origin_certificates(
    db: AsyncSession,
    supplier_id: str,
    certificates: list[dict[str, Any]],
) -> list[str]:
    """
    마스터폼 섹션 3의 원산지 인증서 데이터를 origin_certificates 테이블에 삽입한다.

    [호출 흐름]
      POST /suppliers/{id}/master-form
        → B의 supplier/service.py (마스터폼 진입점)
          → 이 함수 호출 (섹션 3 원산지 인증서 분배)
          → 단일 트랜잭션 commit (B의 service가 담당)

    [왜 ORM이 아닌 raw SQL을 쓰는가]
      origin_certificates 테이블의 ORM 모델(OriginCertificate)은
      B(은진) 담당의 supplier/models.py에 정의돼 있다.
      경계 규칙(남의 도메인 import 금지)을 지키기 위해
      raw SQL(text())로 INSERT한다.
      compliance.py의 _insert_compliance_result()과 동일한 패턴.

    [중요 — flush만, commit 금지]
      이 함수는 마스터폼의 단일 트랜잭션 내에서 호출된다.
      flush(): SQL을 DB에 전송하되 트랜잭션은 열어둔다.
      commit(): B의 service가 모든 섹션 write 완료 후 일괄 수행.
      한 섹션이라도 실패하면 전체 rollback (atomic 보장).

    [파라미터]
      db            : SQLAlchemy 비동기 세션
      supplier_id   : 마스터폼 제출 주체인 협력사의 UUID 문자열
      certificates  : MasterFormOriginCert Pydantic 모델을 dict로 변환한 리스트
                      각 항목 필드:
                        cert_type          (str)  : FTA/GSP/UFLPA_REBUTTAL/IRA_ORIGIN/CONFLICT_FREE/GENERAL
                        cert_number        (str?) : 인증서 번호
                        issuing_authority  (str?) : 발급 기관
                        issued_at          (date?): 발급일
                        expires_at         (date) : 만료일 (필수 — 12개월 자동 검증 대상)
                        origin_country     (str?) : ISO 3166-1 alpha-2 국가 코드
                        covered_minerals   (dict?): 적용 광물 종류 JSONB
                        document_url       (str?) : 증빙 문서 URL

    [반환]
      생성된 cert_id UUID 문자열 리스트. B의 service가 응답에 포함할 수 있도록.

    [사용 예시 — B의 service에서]
      from backend.domains.regulation.repository import write_origin_certificates

      cert_ids = await write_origin_certificates(
          db=db,
          supplier_id=str(supplier_id),
          certificates=[cert.model_dump() for cert in form.origin.origin_certificates],
      )
    """
    created_ids: list[str] = []

    for cert in certificates:
        cert_id = str(uuid.uuid4())

        await db.execute(
            text("""
                INSERT INTO origin_certificates
                    (cert_id, supplier_id,
                     cert_type, cert_number, issuing_authority,
                     issued_at, expires_at,
                     origin_country, covered_minerals,
                     status, document_url,
                     created_at, updated_at)
                VALUES
                    (:cert_id, :supplier_id::uuid,
                     :cert_type, :cert_number, :issuing_authority,
                     :issued_at, :expires_at,
                     :origin_country, CAST(:covered_minerals AS jsonb),
                     'valid', :document_url,
                     :now, :now)
            """),
            {
                "cert_id":            cert_id,
                "supplier_id":        supplier_id,
                "cert_type":          cert["cert_type"],
                "cert_number":        cert.get("cert_number"),
                "issuing_authority":  cert.get("issuing_authority"),
                "issued_at":          cert.get("issued_at"),
                "expires_at":         cert["expires_at"],
                "origin_country":     cert.get("origin_country"),
                "covered_minerals":   json.dumps(
                    cert.get("covered_minerals"),
                    ensure_ascii=False,
                ) if cert.get("covered_minerals") else None,
                "document_url":       cert.get("document_url"),
                "now":                datetime.now(timezone.utc),
            },
        )

        created_ids.append(cert_id)

        logger.info(
            "원산지 인증서 INSERT: cert_id=%s, supplier_id=%s, cert_type=%s",
            cert_id, supplier_id, cert["cert_type"],
        )

    # flush: SQL을 DB에 전송하되 커밋은 하지 않는다.
    # B의 service가 모든 섹션 write 완료 후 commit() 일괄 수행.
    await db.flush()

    logger.info(
        "원산지 인증서 %d건 flush 완료 (supplier_id=%s). commit은 상위 service에서.",
        len(created_ids), supplier_id,
    )

    return created_ids