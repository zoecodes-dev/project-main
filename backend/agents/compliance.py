"""
agents/compliance.py — Compliance Interpreter Agent (은지 / C)

역할:
  BatchState의 verification_result를 받아, 목적지(destination)별로 적용되는
  규제 각각의 준수 여부를 판정하고 compliance_results 테이블에 기록한다.

Day3 완성 상태:
  - REGULATION_BY_DESTINATION: supervisor가 import하는 매핑 딕셔너리 (Day1)
  - generate_embedding(): 텍스트 → Bedrock Cohere Embed v4 벡터 변환 (Day2)
  - search_regulations(): pgvector 코사인 유사도 RAG 검색 (Day2)
  - _call_sonnet_for_verdict(): Sonnet 호출 래퍼 — cited_clauses 강제 (Day3)
  - judge_uflpa(): UFLPA 전용 judge — @trace_tool("compliance_judge_UFLPA") (Day3)
  - judge_ira(): IRA 전용 judge — @trace_tool("compliance_judge_IRA") (Day3)
  - judge_generic(): 나머지 실판정 5종 공통 judge (Day3)
  - _stub_passed_judge(): CBAM / CONFLICT_MINERALS / CRMA 깡통 judge (Day3)
  - REGULATION_JUDGES: regulation_code → judge 함수 매핑 딕셔너리 (Day3)
  - _insert_compliance_result(): compliance_results INSERT 헬퍼 (Day3)
  - compliance_node: 실판정 버전 — Day1 skeleton 교체 (Day3)

[변경 이력]
  - ComplianceCompleted dataclass 제거 → events/types.py의 ComplianceCompletedEvent 사용
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
from backend.events.types import ComplianceCompletedEvent
from backend.infrastructure.database import AsyncSessionLocal
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node, trace_tool
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
    # EU 배터리법(전체/Art.7/Art.47), 산림파괴방지법, 공급망실사, 탄소국경, 분쟁광물, 핵심원자재
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
    # 위구르 강제노동방지법, IRA FEOC, 분쟁광물(EU·US 공통 적용)
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
# 2. RAG 도구 함수 (Day2 — 그대로 유지)
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

    반환 예시:
      [
        {
          "regulation_id": "...",
          "regulation_code": "UFLPA",
          "name": "Uyghur Forced Labor Prevention Act",
          "description": "...",
          "similarity": 0.92,
        },
        ...
      ]

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
# 3. Sonnet 호출 래퍼 (Day3)
#    RAG로 가져온 조항 + 협력사 데이터를 Sonnet에게 주고 JSON 판정을 받는다.
#    cited_clauses 가 비어 있으면 호출부(judge_*)에서 compliance_reject 처리.
# ---------------------------------------------------------------------------

_SONNET_MODEL = "global.anthropic.claude-sonnet-4-6"


def _get_anthropic_key() -> str:
    from backend.core.config import config
    return config.ANTHROPIC_API_KEY


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
# 4. Stub judge — CBAM / CONFLICT_MINERALS / CRMA (Day3)
#    항상 compliance_passed 반환. Sonnet 호출 없음.
#    cited_clauses도 stub으로 채워요 — 빈 리스트면 INSERT 헬퍼의
#    "cited_clauses 누락" 경고 경로로 빠질 수 있어서요.
# ---------------------------------------------------------------------------

_STUB_REGULATIONS: set[str] = {"CBAM", "CONFLICT_MINERALS", "CRMA"}


async def _stub_passed_judge(regulation_code: str) -> dict:
    return {
        "verdict": "compliance_passed",
        "needs_human_review": False,
        "cited_clauses": [
            {"citation": f"{regulation_code} §stub", "content": "stub — always passed"}
        ],
        "confidence_score": 1.0,
        "reasoning_text": f"{regulation_code} is a stub judge — automatically passed.",
    }


# ---------------------------------------------------------------------------
# 5. 규제별 전용 judge 함수 (Day3)
#    geo_audit.py 패턴 준수: 기능별 함수 분리 + 고정 이름 @trace_tool.
#    시연 핵심인 UFLPA·IRA는 전용 함수, 나머지 실판정 5종은 judge_generic.
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


# 나머지 실판정 5종 RAG 쿼리 힌트
_GENERIC_QUERY_HINTS: dict[str, str] = {
    "EU_BATTERY":       "Annex XIII battery passport mandatory data fields",
    "EU_BATTERY_ART7":  "carbon footprint declaration lifecycle threshold battery",
    "EU_BATTERY_ART47": "supply chain due diligence policy battery manufacturer",
    "EUDR":             "deforestation GPS polygon forest risk commodity operator FSC",
    "CSDDD":            "child labor forced labor human rights due diligence supply chain",
}


@trace_tool("compliance_judge_generic")
async def judge_generic(
    batch_id: str, regulation_code: str, context: dict, db: AsyncSession
) -> dict:
    """
    EU_BATTERY / EU_BATTERY_ART7 / EU_BATTERY_ART47 / EUDR / CSDDD 공통 judge.
    UFLPA·IRA처럼 시연 핵심은 아니지만 실판정 경로로 동작한다.
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
# 6. REGULATION_JUDGES — regulation_code → judge 함수 매핑 (Day3)
#    compliance_node가 이 딕셔너리로 올바른 judge를 선택한다.
#    spec C-3의 REGULATION_JUDGES 구조를 그대로 따른다.
# ---------------------------------------------------------------------------

REGULATION_JUDGES: dict[str, Callable] = {
    # 시연 핵심 — 전용 judge
    "UFLPA":             judge_uflpa,
    "IRA":               judge_ira,
    # 실판정 5종 — 공통 judge (regulation_code를 인자로 넘김)
    "EU_BATTERY":        judge_generic,
    "EU_BATTERY_ART7":   judge_generic,
    "EU_BATTERY_ART47":  judge_generic,
    "EUDR":              judge_generic,
    "CSDDD":             judge_generic,
    # Stub 3종 — 항시 compliance_passed
    "CBAM":              _stub_passed_judge,
    "CONFLICT_MINERALS": _stub_passed_judge,
    "CRMA":              _stub_passed_judge,
}


# ---------------------------------------------------------------------------
# 7. compliance_results INSERT 헬퍼 (Day3)
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
                 :verdict, :needs_human_review, :cited_clauses::jsonb,
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
# 8. judge context 빌더 (Day3)
#    앞 단계(extraction, verification, geo) 결과를 합쳐
#    judge에게 넘길 컨텍스트 dict를 구성한다.
#    없는 키는 빈값으로 채워요(KeyError 방지).
# ---------------------------------------------------------------------------

def _build_judge_context(state: BatchState) -> dict:
    extraction:   dict = state.get("extraction_result")   or {}
    verification: dict = state.get("verification_result") or {}
    geo:          dict = state.get("geo_result")          or {}

    return {
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
    }


# ---------------------------------------------------------------------------
# 9. compliance_node — 실판정 버전 (Day3, Day1 skeleton 교체)
#
#     graph.py 패턴: state 하나만 인자로 받음.
#     DB 세션은 내부에서 AsyncSessionLocal로 직접 연다.
#     (지혜의 hitl_interrupt_node와 동일한 패턴)
# ---------------------------------------------------------------------------

@trace_node("compliance", "agent")
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

            # stub(2-인자)과 전용/generic(3~4인자)의 시그니처가 달라서 분기해요
            if reg_code in _STUB_REGULATIONS:
                result = await judge_fn(reg_code)
            elif reg_code in ("UFLPA", "IRA"):
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
    # events/types.py의 ComplianceCompletedEvent 사용
    await publish(
        "ComplianceCompleted",
        dataclasses.asdict(ComplianceCompletedEvent(batch_id=batch_id, verdicts=verdicts)),
    )

    return {
        **state,
        "current_stage":    "stage_compliance",
        "confidence_score": new_confidence,
        "compliance_result": {
            "verdicts":           verdicts,
            "needs_human_review": any_human_review,
            "evaluated_at":       datetime.now(timezone.utc).isoformat(),
        },
    }
