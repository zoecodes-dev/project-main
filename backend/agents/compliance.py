"""
agents/compliance.py — Compliance Interpreter Agent (은지 / C)

역할:
  BatchState의 verification_result를 받아, 목적지(destination)별로 적용되는
  규제 각각의 준수 여부를 판정하고 compliance_results 테이블에 기록한다.

Day3 완성 상태:
  - REGULATION_BY_DESTINATION: supervisor가 import하는 매핑 딕셔너리 (Day1)
  - generate_embedding(): 텍스트 → Bedrock Cohere Embed v4 벡터 변환 (Day2)
  - search_regulations(): pgvector 코사인 유사도 RAG 검색 (Day2)
  - ComplianceCompleted: 이벤트 dataclass (Day3)
  - _call_sonnet_for_verdict(): Sonnet 호출 래퍼 — cited_clauses 강제 (Day3)
  - judge_uflpa(): UFLPA 전용 judge — @trace_tool("compliance_judge_UFLPA") (Day3)
  - judge_ira(): IRA 전용 judge — @trace_tool("compliance_judge_IRA") (Day3)
  - judge_generic(): 나머지 실판정 3종 공통 judge (Day3)
  - _stub_passed_judge(): CBAM / CONFLICT_MINERALS / CRMA 깡통 judge (Day3)
  - REGULATION_JUDGES: regulation_code → judge 함수 매핑 딕셔너리 (Day3)
  - _insert_compliance_result(): compliance_results INSERT 헬퍼 (Day3)
  - compliance_node: 실판정 버전 — Day1 skeleton 교체 (Day3)

W4_목 추가 작업:
  - _CARBON_THRESHOLD_VIOLATION / _CARBON_THRESHOLD_WARNING: 탄소발자국 임계치 상수
  - _RECYCLED_CONTENT_MIN: 재활용 함량 최소 임계치 상수 (광물별)
  - judge_carbon_footprint(): EU_BATTERY_ART7 탄소발자국 선언 검증
      @trace_tool("compliance_judge_CARBON")
      factory_carbon_declarations 테이블 기반 가중평균 조회
      선언 누락 공장 있으면 needs_human_review=True
  - judge_recycled_content(): EU_BATTERY 재활용 함량 검증
      @trace_tool("compliance_judge_RECYCLED")
      recycled_content_ratio + recycled_materials JSONB 광물별 임계치 비교
      효율(%) 검증 미구현 — 12월 시행 전 스프린트에서 처리
  - REGULATION_JUDGES 갱신: EU_BATTERY → judge_recycled_content,
                             EU_BATTERY_ART7 → judge_carbon_footprint
  - _build_judge_context() 갱신: recycled_content_ratio / recycled_materials 키 추가
  - compliance_node @trace_node 데코레이터 제거 — graph 래퍼가 기록 담당
"""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Callable

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.state import BatchState
from backend.infrastructure.database import AsyncSessionLocal
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_tool
from backend.llm.embedding_factory import embed_query

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. 목적지별 적용 규제 매핑
#    - 키: batches.destination 값과 1:1 (schema.sql chk_batch_destination)
#    - 값: regulations.regulation_code 와 정확히 일치하는 문자열 리스트
#    - supervisor.py 가 이 딕셔너리를 직접 import하므로 이름·철자가 SSOT
# ---------------------------------------------------------------------------

REGULATION_BY_DESTINATION: dict[str, list[str]] = {
    # EU 시장 진입 제품 — EU 규제 8종
    "EU": [
        "EU_BATTERY",
        "EU_BATTERY_ART7",
        "EU_BATTERY_ART47",
        "EUDR",
        "CSDDD",
        "CBAM",
        "CONFLICT_MINERALS",
        "CRMA",
    ],
    # 미국 시장 진입 제품 — US 규제 3종
    "US": [
        "UFLPA",
        "IRA",
        "CONFLICT_MINERALS",
    ],
    # 국내(KR) 출하 — 현재 글로벌 규제 검사 대상 없음, 자동 패스
    "KR": [],
    # EU·US 동시 납품 — 합집합 (CONFLICT_MINERALS 중복 1회)
    "BOTH": [
        "EU_BATTERY",
        "EU_BATTERY_ART7",
        "EU_BATTERY_ART47",
        "EUDR",
        "CSDDD",
        "CBAM",
        "CONFLICT_MINERALS",
        "CRMA",
        "UFLPA",
        "IRA",
    ],
}


# ---------------------------------------------------------------------------
# 2. 이벤트 dataclass (Day3)
#    - events/types.py 에 동일 구조가 정의돼야 해요. 여기선 발행용으로만 씀.
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ComplianceCompleted:
    batch_id: str
    verdicts: dict[str, str]   # {regulation_code: verdict 문자열}


# ---------------------------------------------------------------------------
# 3. RAG 도구 함수 (Day2 — 그대로 유지)
# ---------------------------------------------------------------------------

@trace_tool("generate_embedding")
async def generate_embedding(input_text: str) -> list[float]:
    """
    텍스트 → Bedrock Cohere Embed v4 벡터 변환.
    embedding_factory.embed_query()를 래핑한다.
    - seed_regulations.py 와 search_regulations() 양쪽에서 공유한다.
    """
    return embed_query(input_text)


@trace_tool("regulation_rag_search")
async def search_regulations(
    query_text: str,
    regulation_code: str,
    db: AsyncSession,
    top_k: int = 3,
) -> list[dict]:
    """
    pgvector 코사인 유사도로 관련 규제 조항을 검색한다.

    동작 흐름:
      1. query_text 를 벡터로 변환 (generate_embedding 호출)
      2. regulations 테이블에서 regulation_code 필터 + embedding_status='indexed' 조건으로
         코사인 거리(<=> 연산자)가 가장 작은(= 의미가 가장 가까운) row top_k 개 반환
      3. 각 row를 dict로 변환해 judge 함수에 전달

    주의:
      - embedding_status = 'indexed' 인 row만 검색 대상이다.
        seed_regulations.py 를 먼저 실행해 임베딩을 생성해야 한다.
      - idx_regulations_embedding (hnsw, vector_cosine_ops) 인덱스가
        schema.sql에 이미 정의돼 있어 대용량에도 빠르게 동작한다.
    """
    query_vector = await generate_embedding(query_text)

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
                "query_vector": str(query_vector),
                "regulation_code": regulation_code,
                "top_k": top_k,
            },
        )
    ).fetchall()

    return [
        {
            "regulation_id": row.regulation_id,
            "regulation_code": row.regulation_code,
            "name": row.name,
            "description": row.description,
            "similarity": float(row.similarity),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 4. Sonnet 호출 래퍼 (Day3)
#    RAG로 가져온 조항 + 협력사 데이터를 Sonnet에게 주고 JSON 판정을 받는다.
#    cited_clauses 가 비어 있으면 호출부(judge_*)에서 compliance_reject 처리.
# ---------------------------------------------------------------------------

_SONNET_MODEL = "global.anthropic.claude-sonnet-4-6"


def _get_anthropic_key() -> str:
    from backend.core.config import settings
    return settings.ANTHROPIC_API_KEY


async def _call_sonnet_for_verdict(
    regulation_code: str,
    clauses: list[dict],
    context: dict,
) -> dict:
    """
    반환 JSON 스키마:
    {
      "verdict": "compliance_passed|compliance_violation|compliance_warning|compliance_reject",
      "cited_clauses": [{"citation": "조항번호", "content": "조항내용"}],
      "confidence_score": 0.0~1.0,
      "reasoning_text": "판단 근거"
    }

    CRITICAL:
      - cited_clauses MUST NOT be empty.
        비어 있으면 judge_*가 compliance_reject + needs_human_review=True 로 처리.
      - verdict는 4종 중 하나, 언더스코어 표기. (schema.sql chk_compliance_verdict)
    """
    clauses_text = "\n".join(
        f"[{i+1}] {c.get('name', regulation_code)} — {c.get('description', '')}"
        for i, c in enumerate(clauses)
    ) or (
        "(관련 조항을 찾지 못했어요. "
        "cited_clauses를 비우지 말고 verdict='compliance_reject'으로 판정하세요.)"
    )

    system_prompt = (
        "You are a compliance verification engine for battery supply chain regulations. "
        "Respond with a JSON object ONLY — no text outside JSON, no markdown fences.\n\n"
        "Required schema:\n"
        "{\n"
        '  "verdict": "<compliance_passed|compliance_violation|compliance_warning|compliance_reject>",\n'
        '  "cited_clauses": [{"citation": "<article ref>", "content": "<clause text>"}],\n'
        '  "confidence_score": <0.0–1.0>,\n'
        '  "reasoning_text": "<explanation>"\n'
        "}\n\n"
        "CRITICAL RULES:\n"
        "1. cited_clauses MUST NOT be empty. "
        "If no clause can be identified, set verdict='compliance_reject' and explain in reasoning_text.\n"
        "2. Do NOT invent clauses — only cite from the provided context.\n"
        "3. verdict must be exactly one of the four values (underscore, no hyphen)."
    )

    user_prompt = (
        f"Regulation: {regulation_code}\n\n"
        f"Relevant clauses:\n{clauses_text}\n\n"
        f"Supplier/batch data:\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "Return the JSON judgment."
    )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": _get_anthropic_key(),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _SONNET_MODEL,
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=60.0,
        )
        resp.raise_for_status()

    raw = resp.json()["content"][0]["text"].strip()

    # Sonnet이 ```json 블록으로 감싸는 경우 방어
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw.strip())


def _validate_cited_clauses(result: dict, regulation_code: str) -> dict:
    """
    cited_clauses 검증 공통 헬퍼.
    비어 있으면 compliance_reject + needs_human_review=True 로 보정한다.
    """
    if not result.get("cited_clauses"):
        result["verdict"] = "compliance_reject"
        result["needs_human_review"] = True
        result["reasoning_text"] = (
            "[cited_clauses 누락] Sonnet이 근거 조항을 제시하지 못했어요. "
            + result.get("reasoning_text", "")
        )
    result.setdefault("needs_human_review", False)
    result.setdefault("confidence_score", 0.9)
    return result


# ---------------------------------------------------------------------------
# 5. Stub judge — CBAM / CONFLICT_MINERALS / CRMA (Day3)
#    항상 compliance_passed 반환. Sonnet 호출 없음.
# ---------------------------------------------------------------------------

_STUB_REGULATIONS: set[str] = {"CBAM", "CONFLICT_MINERALS", "CRMA"}

# [BYPASS:A1] 범위 외 규제 자동 통과 스텁 — 범위 확장 시 실판정 교체
async def _stub_passed_judge(regulation_code: str) -> dict:
    return {
        "verdict": "compliance_passed",
        "needs_human_review": False,
        "cited_clauses": [
            {
                "citation": f"{regulation_code} (범위 외)",
                "content": "프로젝트 검증 범위(UFLPA·IRA·EU배터리·EUDR·CSDDD) 외 규제 — 자동 통과 처리",
            }
        ],
        "confidence_score": 1.0,
        "reasoning_text": (
            f"{regulation_code}는 현재 검증 범위 외 규제로 자동 통과 처리되었습니다. "
            "실판정이 필요한 경우 범위 확장이 필요합니다."
        ),
        "is_out_of_scope": True,
    }

# ---------------------------------------------------------------------------
# 5-A. 탄소발자국·재활용 임계치 상수 (Day2)
#
#   하드코딩 금지 원칙에 따라 모두 모듈 상수로 선언.
#   규제 강화 시 이 상수만 변경하면 judge 로직은 수정 불필요.
# ---------------------------------------------------------------------------

# EU 배터리법 2023/1542 Art.7 / Annex II — 탄소발자국 (단위: kgCO2eq/kWh)
# 2025.2 시행 기준. 향후 규제 강화 시 이 두 값만 변경.
_CARBON_THRESHOLD_VIOLATION: float = 100.0  # 초과 시 compliance_violation
_CARBON_THRESHOLD_WARNING:   float = 75.0   # 초과 시 compliance_warning

# EU 배터리법 2023/1542 Annex XII — 재활용 함량 최소 기준 (단위: %)
# key: 소문자 원소기호 (events/types.py RecycledMaterialsSchema 컨벤션 — B·C 공유)
# 2031년 강화 전 현행 기준.
_RECYCLED_CONTENT_MIN: dict[str, float] = {
    "co": 16.0,  # 코발트
    "ni":  6.0,  # 니켈
    "li":  6.0,  # 리튬
    "pb": 85.0,  # 납
}


# ---------------------------------------------------------------------------
# 6. 규제별 전용 judge 함수
#    geo_audit.py 패턴 준수: 기능별 함수 분리 + 고정 이름 @trace_tool.
# ---------------------------------------------------------------------------

@trace_tool("compliance_judge_UFLPA")
async def judge_uflpa(batch_id: str, context: dict, db: AsyncSession) -> dict:
    """
    UFLPA (위구르 강제노동방지법) 판정.
    신장(Xinjiang) 원산지 원자재가 포함된 경우 rebuttable presumption 적용.
    origin_country = 'CN' + geo_risk_flags에 xinjiang 포함 시 compliance_violation.
    """
    clauses = await search_regulations(
        "Xinjiang forced labor origin country supply chain prohibition rebuttable presumption",
        "UFLPA",
        db,
        top_k=5,
    )
    try:
        result = await _call_sonnet_for_verdict("UFLPA", clauses, context)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
        return {
            "verdict": "compliance_reject",
            "needs_human_review": True,
            "cited_clauses": [],
            "confidence_score": 0.0,
            "reasoning_text": f"Sonnet 호출 실패: {exc}",
        }
    return _validate_cited_clauses(result, "UFLPA")


@trace_tool("compliance_judge_IRA")
async def judge_ira(batch_id: str, context: dict, db: AsyncSession) -> dict:
    """
    IRA FEOC (인플레이션감축법 — 우려국 외국 기업) 판정.
    FEOC 직접 지분 ≥25% → compliance_violation, needs_human_review=False.
    FEOC 간접 지분 ≥25% → compliance_violation, needs_human_review=True (우회 구조 해석 필요).
    """
    clauses = await search_regulations(
        "FEOC foreign entity of concern ownership threshold 25 percent battery critical mineral",
        "IRA",
        db,
        top_k=5,
    )
    try:
        result = await _call_sonnet_for_verdict("IRA", clauses, context)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
        return {
            "verdict": "compliance_reject",
            "needs_human_review": True,
            "cited_clauses": [],
            "confidence_score": 0.0,
            "reasoning_text": f"Sonnet 호출 실패: {exc}",
        }
    return _validate_cited_clauses(result, "IRA")


@trace_tool("compliance_judge_CARBON")
async def judge_carbon_footprint(
    batch_id: str, context: dict, db: AsyncSession
) -> dict:
    """
    EU 배터리법 Art.7 탄소발자국 선언 검증. (Day2)

    판정 로직:
      1. factory_carbon_declarations 테이블에서 이 배치에 연결된 공장들의
         carbon_intensity 를 supply_ratio.ratio_percentage 로 가중평균 조회.
      2. 선언 누락 공장 수(missing_declaration_count) 확인.
         누락 공장이 있으면 ART7 미충족 → needs_human_review=True.
      3. 가중평균값을 임계치와 비교해 Sonnet에 힌트로 제공 후 판정.

    판정 기준 (EU 2023/1542 Annex II):
      - weighted_carbon_intensity > 100 kgCO2eq/kWh → compliance_violation
      - weighted_carbon_intensity >  75 kgCO2eq/kWh → compliance_warning
      - weighted_carbon_intensity <=  75             → compliance_passed
      - 선언 데이터 전혀 없음                         → compliance_reject + needs_human_review
      - 선언 누락 공장 존재                           → 판정 후 needs_human_review=True 강제

    데이터 출처:
      - factory_carbon_declarations (Day2 신설 테이블)
      - supply_ratio.ratio_percentage (공장별 납품 기여율)
      - batches.bom_version_id → supply_chain_map → supply_ratio 경로
    """
    clauses = await search_regulations(
        "carbon footprint declaration lifecycle threshold kgCO2eq battery cell manufacturing",
        "EU_BATTERY_ART7",
        db,
        top_k=5,
    )

    # (A) 가중평균 탄소집약도 조회
    weighted_row = (await db.execute(
        text("""
            SELECT
                SUM(fcd.carbon_intensity * sr.ratio_percentage)
                    / NULLIF(SUM(sr.ratio_percentage), 0) AS weighted_carbon_intensity
            FROM batches b
            JOIN supply_chain_map scm ON scm.bom_version_id = b.bom_version_id
            JOIN supply_ratio sr      ON sr.map_id = scm.map_id
            JOIN factory_carbon_declarations fcd
                 ON fcd.factory_id = sr.factory_id AND fcd.is_active = TRUE
            WHERE b.batch_id = :batch_id
        """),
        {"batch_id": batch_id},
    )).fetchone()

    weighted_carbon = (
        float(weighted_row.weighted_carbon_intensity)
        if weighted_row and weighted_row.weighted_carbon_intensity is not None
        else None
    )

    # (B) 선언 누락 공장 수 조회
    missing_row = (await db.execute(
        text("""
            SELECT COUNT(*) AS missing_declaration_count
            FROM batches b
            JOIN supply_chain_map scm ON scm.bom_version_id = b.bom_version_id
            JOIN supply_ratio sr      ON sr.map_id = scm.map_id
            LEFT JOIN factory_carbon_declarations fcd
                 ON fcd.factory_id = sr.factory_id AND fcd.is_active = TRUE
            WHERE b.batch_id = :batch_id
              AND fcd.declaration_id IS NULL
        """),
        {"batch_id": batch_id},
    )).fetchone()

    missing_count = int(missing_row.missing_declaration_count) if missing_row else 0

    # 선언 데이터 전혀 없음 → 즉시 reject
    if weighted_carbon is None:
        return {
            "verdict":            "compliance_reject",
            "needs_human_review": True,
            "cited_clauses":      [],
            "confidence_score":   0.0,
            "reasoning_text": (
                "factory_carbon_declarations 에 이 배치와 연결된 선언 데이터가 없어요. "
                "공장별 탄소발자국 선언을 등록해주세요."
            ),
        }

    # Sonnet 컨텍스트 구성
    enriched_context = {
        **context,
        "weighted_carbon_intensity":  weighted_carbon,
        "missing_declaration_count":  missing_count,
        "carbon_threshold_violation": _CARBON_THRESHOLD_VIOLATION,
        "carbon_threshold_warning":   _CARBON_THRESHOLD_WARNING,
        "pre_verdict_hint": (
            "violation" if weighted_carbon > _CARBON_THRESHOLD_VIOLATION
            else "warning" if weighted_carbon > _CARBON_THRESHOLD_WARNING
            else "passed"
        ),
    }

    try:
        result = await _call_sonnet_for_verdict("EU_BATTERY_ART7", clauses, enriched_context)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
        return {
            "verdict":            "compliance_reject",
            "needs_human_review": True,
            "cited_clauses":      [],
            "confidence_score":   0.0,
            "reasoning_text":     f"Sonnet 호출 실패: {exc}",
        }

    result = _validate_cited_clauses(result, "EU_BATTERY_ART7")

    # 선언 누락 공장 있으면 판정과 무관하게 needs_human_review 강제
    if missing_count > 0:
        result["needs_human_review"] = True
        result["reasoning_text"] = (
            f"[선언 누락 {missing_count}개 공장] " + result.get("reasoning_text", "")
        )

    return result


@trace_tool("compliance_judge_RECYCLED")
async def judge_recycled_content(
    batch_id: str, context: dict, db: AsyncSession
) -> dict:
    """
    EU 배터리법 재활용 함량 검증. (Day2)

    판정 기준 (EU 2023/1542 Annex XII):
      - recycled_materials 내 광물별 함량 < _RECYCLED_CONTENT_MIN → compliance_violation
      - recycled_content_ratio 있으나 광물 특정 불가               → compliance_warning
      - 기준 충족                                                   → compliance_passed
      - 데이터 전체 없음                                            → compliance_reject + needs_human_review

    미구현 사항:
      - recycling_efficiency(효율%) 검증 — 2025-12 시행 전 스프린트에서 처리.
        schema에 recycling_efficiency 컬럼 추가됨(Day2). extraction 연동 후 구현 예정.
    """
    clauses = await search_regulations(
        "recycled content minimum threshold cobalt nickel lithium battery Annex XII",
        "EU_BATTERY",
        db,
        top_k=5,
    )

    ratio: float | None = context.get("recycled_content_ratio")
    materials: dict     = context.get("recycled_materials") or {}

    # 사전 판정: 데이터 전체 누락
    if ratio is None and not materials:
        return {
            "verdict":            "compliance_reject",
            "needs_human_review": True,
            "cited_clauses":      [],
            "confidence_score":   0.0,
            "reasoning_text": (
                "recycled_content_ratio 와 recycled_materials 모두 없어요. "
                "supplier_recycler_details 데이터를 확인해주세요."
            ),
        }

    # 광물별 임계치 위반 사전 계산 — Sonnet 힌트로 제공
    threshold_violations: list[str] = [
        f"{mineral.upper()} {materials[mineral]}% < 최소 {min_pct}%"
        for mineral, min_pct in _RECYCLED_CONTENT_MIN.items()
        if mineral in materials and float(materials[mineral]) < min_pct
    ]

    enriched_context = {
        **context,
        "recycled_content_min_thresholds": _RECYCLED_CONTENT_MIN,
        "threshold_violations_hint":       threshold_violations,
        "pre_verdict_hint": "violation" if threshold_violations else "passed",
    }

    try:
        result = await _call_sonnet_for_verdict("EU_BATTERY", clauses, enriched_context)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
        return {
            "verdict":            "compliance_reject",
            "needs_human_review": True,
            "cited_clauses":      [],
            "confidence_score":   0.0,
            "reasoning_text":     f"Sonnet 호출 실패: {exc}",
        }

    return _validate_cited_clauses(result, "EU_BATTERY")


# 나머지 실판정 3종 RAG 쿼리 힌트
_GENERIC_QUERY_HINTS: dict[str, str] = {
    "EU_BATTERY_ART47": "supply chain due diligence policy battery manufacturer",
    "EUDR":             "deforestation GPS polygon forest risk commodity operator FSC",
    "CSDDD":            "child labor forced labor human rights due diligence supply chain",
}


@trace_tool("compliance_judge_generic")
async def judge_generic(
    batch_id: str, regulation_code: str, context: dict, db: AsyncSession
) -> dict:
    """
    EU_BATTERY_ART47 / EUDR / CSDDD 공통 judge.
    UFLPA·IRA·EU_BATTERY·EU_BATTERY_ART7처럼 시연 핵심은 아니지만 실판정 경로로 동작한다.
    """
    query_hint = _GENERIC_QUERY_HINTS.get(regulation_code, regulation_code)
    clauses = await search_regulations(query_hint, regulation_code, db, top_k=5)
    try:
        result = await _call_sonnet_for_verdict(regulation_code, clauses, context)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
        return {
            "verdict": "compliance_reject",
            "needs_human_review": True,
            "cited_clauses": [],
            "confidence_score": 0.0,
            "reasoning_text": f"Sonnet 호출 실패: {exc}",
        }
    return _validate_cited_clauses(result, regulation_code)


# ---------------------------------------------------------------------------
# 7. REGULATION_JUDGES — regulation_code → judge 함수 매핑
#    compliance_node가 이 딕셔너리로 올바른 judge를 선택한다.
# ---------------------------------------------------------------------------

REGULATION_JUDGES: dict[str, Callable] = {
    # 시연 핵심 — 전용 judge
    "UFLPA":            judge_uflpa,
    "IRA":              judge_ira,
    # Day2 신규 — 탄소발자국·재활용 전용 judge
    "EU_BATTERY":       judge_recycled_content,   # Day2: judge_generic → judge_recycled_content
    "EU_BATTERY_ART7":  judge_carbon_footprint,   # Day2: judge_generic → judge_carbon_footprint
    # 실판정 3종 — 공통 judge (regulation_code를 인자로 넘김)
    "EU_BATTERY_ART47": judge_generic,
    "EUDR":             judge_generic,
    "CSDDD":            judge_generic,
    # Stub 3종 — 항시 compliance_passed
    "CBAM":             _stub_passed_judge,
    "CONFLICT_MINERALS":_stub_passed_judge,
    "CRMA":             _stub_passed_judge,
}


# ---------------------------------------------------------------------------
# 8. compliance_results INSERT 헬퍼 (Day3)
# ---------------------------------------------------------------------------

async def _insert_compliance_result(
    db: AsyncSession,
    batch_id: str,
    regulation_code: str,
    supplier_id: str | None,
    result: dict,
) -> None:
    """
    compliance_results 테이블에 판정 결과 1건을 INSERT한다.
    regulation_id는 regulation_code로 조회한다.
    row가 없으면(시드 미적재) 경고 로그만 남기고 건너뛴다.
    """
    reg_row = (await db.execute(
        text("SELECT regulation_id FROM regulations WHERE regulation_code = :code"),
        {"code": regulation_code},
    )).fetchone()

    if reg_row is None:
        logger.warning(
            "regulation_code=%s 에 해당하는 row가 없어요. 시드를 확인해주세요.",
            regulation_code,
        )
        return

    await db.execute(
        text("""
            INSERT INTO compliance_results
                (result_id, batch_id, regulation_id, supplier_id,
                 verdict, needs_human_review, cited_clauses,
                 confidence_score, reasoning_text, created_at)
            VALUES
                (:result_id, :batch_id, :regulation_id, :supplier_id,
                 :verdict, :needs_human_review, CAST(:cited_clauses AS jsonb),
                 :confidence_score, :reasoning_text, :created_at)
        """),
        {
            "result_id":          str(uuid.uuid4()),
            "batch_id":           batch_id,
            "regulation_id":      str(reg_row.regulation_id),
            "supplier_id":        supplier_id,
            "verdict":            result["verdict"],
            "needs_human_review": result.get("needs_human_review", False),
            "cited_clauses":      json.dumps(
                                      result.get("cited_clauses", []),
                                      ensure_ascii=False,
                                  ),
            "confidence_score":   result.get("confidence_score", 1.0),
            "reasoning_text":     result.get("reasoning_text", ""),
            "created_at":         datetime.now(timezone.utc),
        },
    )
    await db.flush()


# ---------------------------------------------------------------------------
# 9. judge context 빌더 (Day3 + Day2 키 추가)
#    앞 단계(extraction, verification, geo) 결과를 합쳐
#    judge에게 넘길 컨텍스트 dict를 구성한다.
#    없는 키는 빈값으로 채워요(KeyError 방지).
# ---------------------------------------------------------------------------

def _build_judge_context(state: BatchState) -> dict:
    extraction:   dict = state.get("extraction_result")   or {}
    verification: dict = state.get("verification_result") or {}
    geo:          dict = state.get("geo_result")          or {}

    return {
        # ── 기존 키 ──
        "batch_id":                state["batch_id"],
        "product_id":              state["product_id"],
        "destination":             state.get("destination", ""),
        "supplier_id":             extraction.get("supplier_id"),
        "origin_country":          extraction.get("origin_country", ""),
        "feoc_direct_ownership":   extraction.get("feoc_direct_ownership"),
        "feoc_indirect_ownership": extraction.get("feoc_indirect_ownership"),
        "carbon_intensity":        extraction.get("carbon_intensity"),
        "mine_coordinates":        geo.get("mine_coordinates"),
        "geo_risk_flags":          geo.get("risk_flags", []),
        "verification_flags":      verification.get("flags", []),
        # ── Day2 신규 키 ──
        # supplier_recycler_details 출처.
        # key 컨벤션: 소문자 원소기호(co/ni/li/pb) — events/types.py RecycledMaterialsSchema SSOT.
        "recycled_content_ratio":  extraction.get("recycled_content_ratio"),
        "recycled_materials":      extraction.get("recycled_materials"),
    }


# ---------------------------------------------------------------------------
# 10. compliance_node — 실판정 버전 (Day3, Day1 skeleton 교체)
#
#     @trace_node 제거 — graph.py 래퍼(traced_graph_node)가 기록 담당.
#     graph.py 패턴: state 하나만 인자로 받음.
#     DB 세션은 내부에서 AsyncSessionLocal로 직접 연다.
# ---------------------------------------------------------------------------

async def compliance_node(state: BatchState) -> BatchState:
    """
    Compliance Interpreter 노드 — Day3 실판정 버전

    수신: stage_geo 완료 후의 BatchState
    처리:
      - REGULATION_JUDGES 딕셔너리로 규제별 judge 함수를 선택해 호출
      - 결과를 compliance_results에 INSERT
      - 하나라도 needs_human_review=True면 confidence를 0.84로 강제 하향
        → supervisor route()가 hitl_interrupt로 분기
      - ComplianceCompleted 이벤트 발행 → 차윤(E) Readiness 재계산
    반환: 갱신된 BatchState (이후 supervisor → risk_scoring 또는 hitl_interrupt)
    """
    batch_id:   str       = state["batch_id"]
    applicable: list[str] = state.get("applicable_regulations") or []

    # KR 또는 빈 목록 → 즉시 패스 (DB·Sonnet 호출 없음)
    if not applicable:
        return {
            **state,
            "current_stage": "stage_compliance",
            "compliance_result": {
                "verdicts":     {},
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
                "skipped":      True,
            },
        }

    context     = _build_judge_context(state)
    supplier_id = context.get("supplier_id")

    verdicts: dict[str, str] = {}
    any_human_review = False

    async with AsyncSessionLocal() as db:
        for reg_code in applicable:
            judge_fn = REGULATION_JUDGES.get(reg_code)
            if judge_fn is None:
                logger.warning("regulation_code=%s 에 매핑된 judge가 없어요.", reg_code)
                continue

            # stub(2-인자) / Day2 전용(3-인자) / UFLPA·IRA 전용(3-인자) / generic(4-인자) 분기
            if reg_code in _STUB_REGULATIONS:
                result = await judge_fn(reg_code)
            elif reg_code in ("UFLPA", "IRA", "EU_BATTERY_ART7", "EU_BATTERY"):
                result = await judge_fn(batch_id, context, db)
            else:
                result = await judge_fn(batch_id, reg_code, context, db)

            verdicts[reg_code] = result["verdict"]
            if result.get("needs_human_review"):
                any_human_review = True

            await _insert_compliance_result(db, batch_id, reg_code, supplier_id, result)

        await db.commit()

    # needs_human_review=True → confidence 0.84 강제 하향
    # supervisor: confidence < 0.85 → hitl_interrupt 분기
    new_confidence: float = (
        0.84 if any_human_review else float(state.get("confidence_score") or 1.0)
    )

    # ComplianceCompleted 이벤트 발행 → 차윤(E) Readiness 재계산 트리거
    await publish(
        "ComplianceCompleted",
        dataclasses.asdict(ComplianceCompleted(batch_id=batch_id, verdicts=verdicts)),
    )

    # dpp/service.py의 ESG Score 계산에 필요한 count 키 추가
    passed_count  = sum(1 for v in verdicts.values() if v == "compliance_passed")
    warning_count = sum(1 for v in verdicts.values() if v == "compliance_warning")

    return {
        **state,
        "current_stage":    "stage_compliance",
        "confidence_score": new_confidence,
        "compliance_result": {
            "verdicts":           verdicts,
            "needs_human_review": any_human_review,
            "evaluated_at":       datetime.now(timezone.utc).isoformat(),
            "passed":    passed_count,
            "gray_zone": warning_count,
        },
    }


# ---------------------------------------------------------------------------
# 11. HITL context용 규제 판정 이력 조회 함수 (W4 수 신규)
#
#     차윤(E)의 backend/hitl/service.py 의 get_review_context() 가
#     compliance 도메인을 직접 import하지 않도록 이 함수만 호출해요.
#
#     【차윤과 합의할 시그니처】
#       from backend.agents.compliance import get_compliance_history_for_batch
#       compliance_history = await get_compliance_history_for_batch(db, batch_id)
#       context_data["compliance_history"] = compliance_history
# ---------------------------------------------------------------------------

@trace_tool("get_compliance_history")
async def get_compliance_history_for_batch(
    db: AsyncSession,
    batch_id: uuid.UUID | str,
) -> list[dict]:
    """
    HITL 검토 화면의 compliance_history 섹션 데이터를 제공해요.

    compliance_results + regulations 조인으로
    판정(verdict) · 신뢰도 · 인용 조항 · 근거 텍스트를 반환해요.
    검토자가 "왜 이 판정인지" 볼 수 있는 최소 필드로 구성했어요.

    정렬: created_at DESC (최신 판정이 위로)
    """
    rows = (
        await db.execute(
            text("""
                SELECT
                    r.regulation_code,
                    r.name                AS regulation_name,
                    cr.verdict,
                    cr.needs_human_review,
                    cr.confidence_score,
                    cr.cited_clauses,
                    cr.reasoning_text,
                    cr.created_at
                FROM compliance_results cr
                JOIN regulations r
                  ON r.regulation_id = cr.regulation_id
                WHERE cr.batch_id = :batch_id
                ORDER BY cr.created_at DESC
            """),
            {"batch_id": str(batch_id)},
        )
    ).fetchall()

    return [
        {
            "regulation_code":    row.regulation_code,
            "regulation_name":    row.regulation_name,
            "verdict":            row.verdict,
            "needs_human_review": row.needs_human_review,
            "confidence_score":   (
                float(row.confidence_score)
                if row.confidence_score is not None
                else None
            ),
            "cited_clauses":      row.cited_clauses or [],
            "reasoning_text":     row.reasoning_text or "",
            "created_at":         (
                row.created_at.isoformat()
                if row.created_at else None
            ),
        }
        for row in rows
    ]