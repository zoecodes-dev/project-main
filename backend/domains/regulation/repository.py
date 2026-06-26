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

  ── [신규 §2.3a / §7.1] ──
  get_violations()            : compliance_results(verdict=violation) 조회 (tenant 격리)
  get_regulation_results()    : compliance_results 전체 조회 (HITL 포함, tenant 격리)
  count_violations()          : get_violations 필터 동일, 전체 건수만
  count_regulation_results()  : get_regulation_results 필터 동일, 전체 건수만

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
    stmt = select(Regulation).order_by(Regulation.regulation_code)

    if region is not None:
        stmt = stmt.where(Regulation.region == region)

    result = await db.execute(stmt)
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

    [destination → region 매핑 규칙]
      "EU"   → region = 'EU'  인 규제
      "US"   → region = 'US'  인 규제
      "BOTH" → region IN ('EU', 'US', 'BOTH') — 합집합
      "KR"   → 빈 리스트 (국내 출하는 글로벌 규제 검사 대상 없음)
    """
    if destination == "KR":
        return []

    stmt = select(Regulation).order_by(Regulation.regulation_code)

    if destination == "BOTH":
        stmt = stmt.where(Regulation.region.in_(["EU", "US", "BOTH"]))
    else:
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

    [주의]
      - embedding_status='indexed' 인 row만 검색 대상.
      - lazy import: Bedrock 미연결 환경에서 무관 엔드포인트 타임아웃 방지.
    """
    from backend.llm.embedding_factory import embed_query  # noqa: PLC0415

    query_vector: list[float] = embed_query(query_text)

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
    현재는 더미 데이터 반환.
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

    [중요 — flush만, commit 금지]
      이 함수는 마스터폼의 단일 트랜잭션 내에서 호출된다.
      B의 service가 모든 섹션 write 완료 후 commit() 일괄 수행.
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

    await db.flush()

    logger.info(
        "원산지 인증서 %d건 flush 완료 (supplier_id=%s). commit은 상위 service에서.",
        len(created_ids), supplier_id,
    )

    return created_ids


# ============================================================
# 5. [신규 §2.3a] compliance_results 위반 목록 조회
#    ★ tenant 격리: compliance_results에 tenant_id 없음
#      → batches JOIN으로 batches.tenant_id 필터링
# ============================================================

# severity 매핑: compliance_results의 reasoning_text 등 추가 컨텍스트가 없으므로
# regulation.region 기반으로 휴리스틱 매핑.
# EU 규제(EUDR, EU_BATTERY 등) 위반 → critical
# US 규제(IRA, UFLPA 등) 위반 → high
# 나머지 → minor
_REGION_TO_SEVERITY: dict[str, str] = {
    "EU":   "critical",
    "US":   "high",
    "BOTH": "high",
}


def _map_severity(region: Optional[str]) -> str:
    """regulation.region → severity(critical/high/minor) 매핑."""
    return _REGION_TO_SEVERITY.get(region or "", "minor")


async def get_violations(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    supplier_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    위반(verdict = 'compliance_violation') 건만 조회한다.

    ★ 보안 핵심: compliance_results에 tenant_id 컬럼이 없으므로
      batches 테이블과 INNER JOIN해 batches.tenant_id = :tenant_id 조건으로
      현재 테넌트 데이터만 노출한다. (dashboard get_dashboard_kpis 동일 패턴)

    [파라미터]
      db          : AsyncSession
      tenant_id   : 현재 로그인 사용자의 테넌트 UUID (get_current_user에서 주입)
      supplier_id : 선택 필터 — 특정 공급사만 볼 때 사용 (§13.1 위임용)
      limit       : 반환 최대 건수 (기본 50)

    [반환]
      violation_id, batch_id, supplier_id, regulation, regulation_label,
      region, severity, summary, detected_at, status 포함 dict 리스트.

    [공유 계약]
      supplier §13.1 GET /suppliers/{id}/violations 도 이 함수를 위임받아 사용.
      supplier_id 인자만 채워서 호출하면 된다.
    """
    params: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "limit":     limit,
    }

    supplier_filter = ""
    if supplier_id is not None:
        supplier_filter = "AND cr.supplier_id = :supplier_id"
        params["supplier_id"] = str(supplier_id)

    sql = text(f"""
        SELECT
            cr.result_id                    AS violation_id,
            cr.batch_id,
            cr.supplier_id,
            r.regulation_code               AS regulation,
            r.name                          AS regulation_label,
            r.region,
            cr.reasoning_text               AS summary,
            cr.created_at                   AS detected_at,
            b.status
        FROM compliance_results cr
        INNER JOIN batches b
            ON cr.batch_id = b.batch_id
           AND b.tenant_id = :tenant_id::uuid      -- ★ tenant 격리 핵심
        LEFT JOIN regulations r
            ON cr.regulation_id = r.regulation_id
        WHERE
            cr.verdict = 'compliance_violation'
            {supplier_filter}
        ORDER BY cr.created_at DESC
        LIMIT :limit
    """)

    rows = (await db.execute(sql, params)).fetchall()

    return [
        {
            "violation_id":      str(row.violation_id),
            "batch_id":          str(row.batch_id),
            "supplier_id":       str(row.supplier_id),
            "regulation":        row.regulation,
            "regulation_label":  row.regulation_label,
            "region":            row.region,
            "severity":          _map_severity(row.region),
            "summary":           row.summary,
            "detected_at":       row.detected_at.isoformat() if row.detected_at else None,
            "status":            row.status,
        }
        for row in rows
    ]


async def count_violations(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    supplier_id: Optional[uuid.UUID] = None,
) -> int:
    """
    get_violations과 동일한 필터로 전체 건수만 반환한다.
    X-Total-Count 헤더 계산용.
    """
    params: dict[str, Any] = {"tenant_id": str(tenant_id)}

    supplier_filter = ""
    if supplier_id is not None:
        supplier_filter = "AND cr.supplier_id = :supplier_id"
        params["supplier_id"] = str(supplier_id)

    sql = text(f"""
        SELECT COUNT(*) AS total
        FROM compliance_results cr
        INNER JOIN batches b
            ON cr.batch_id = b.batch_id
           AND b.tenant_id = :tenant_id::uuid
        WHERE
            cr.verdict = 'compliance_violation'
            {supplier_filter}
    """)

    result = await db.execute(sql, params)
    return result.scalar() or 0


# ============================================================
# 6. [신규 §7.1] compliance_results 전체 조회 (materials 규제 결과)
#    ★ tenant 격리: 동일하게 batches JOIN
# ============================================================

# verdict 정규화: DB 저장값 → 프론트 표기 4종
_VERDICT_MAP: dict[str, str] = {
    "compliance_passed":    "passed",
    "compliance_violation": "violation",
    "compliance_warning":   "warning",
    "compliance_reject":    "reject",
}

# HITL 후보 기준 (confidence < 0.85)
_HITL_THRESHOLD = 0.85


async def get_regulation_results(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    size: int = 20,
) -> list[dict[str, Any]]:
    """
    규제 판정 전체 목록(§7.1 /materials/regulation-results)을 조회한다.

    ★ 보안 핵심: batches INNER JOIN으로 tenant 격리 (get_violations와 동일 패턴).

    [HITL 후보 식별]
      confidence_score < 0.85 인 row에 needs_human_review = True 플래그.
      confidence_score는 batches 테이블의 confidence_score 컬럼 활용.

    [반환 필드]
      result_id, material, supplier_id, supplier_name, regulation,
      verdict(passed/violation/warning/reject), confidence,
      needs_human_review(bool), evidence(배열)

    [evidence 구조]
      compliance_results에 별도 evidence 테이블이 없으므로
      현재는 빈 배열로 반환. 추후 submission_documents 연계 시 교체.
    """
    offset = (page - 1) * size
    params: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "limit":     size,
        "offset":    offset,
    }

    sql = text("""
        SELECT
            cr.result_id,
            b.product_id                    AS material,   -- 현재 product_id를 material 식별자로 사용
            cr.supplier_id,
            s.company_name                  AS supplier_name,
            r.regulation_code               AS regulation,
            cr.verdict,
            b.confidence_score              AS confidence,
            cr.needs_human_review
        FROM compliance_results cr
        INNER JOIN batches b
            ON cr.batch_id = b.batch_id
           AND b.tenant_id = :tenant_id::uuid      -- ★ tenant 격리 핵심
        LEFT JOIN regulations r
            ON cr.regulation_id = r.regulation_id
        LEFT JOIN suppliers s
            ON cr.supplier_id = s.supplier_id
        ORDER BY cr.created_at DESC
        LIMIT :limit OFFSET :offset
    """)

    rows = (await db.execute(sql, params)).fetchall()

    return [
        {
            "result_id":          str(row.result_id),
            "material":           str(row.material) if row.material else None,
            "supplier_id":        str(row.supplier_id),
            "supplier_name":      row.supplier_name,
            "regulation":         row.regulation,
            "verdict":            _VERDICT_MAP.get(row.verdict, row.verdict),
            "confidence":         float(row.confidence) if row.confidence is not None else None,
            # confidence가 없거나 임계값 미만이면 HITL 후보
            "needs_human_review": (
                row.needs_human_review
                or (row.confidence is not None and float(row.confidence) < _HITL_THRESHOLD)
            ),
            "evidence":           [],  # TODO: submission_documents 연계 후 채움
        }
        for row in rows
    ]


async def count_regulation_results(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> int:
    """
    get_regulation_results와 동일한 필터로 전체 건수만 반환한다.
    X-Total-Count 헤더 계산용.
    """
    sql = text("""
        SELECT COUNT(*) AS total
        FROM compliance_results cr
        INNER JOIN batches b
            ON cr.batch_id = b.batch_id
           AND b.tenant_id = :tenant_id::uuid
    """)
    result = await db.execute(sql, {"tenant_id": str(tenant_id)})
    return result.scalar() or 0