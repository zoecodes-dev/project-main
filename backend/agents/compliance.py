"""
agents/compliance.py — Compliance Interpreter Agent (은지 / C)

역할:
  BatchState의 verification_result를 받아, 목적지(destination)별로 적용되는
  규제 각각의 준수 여부를 판정하고 compliance_results 테이블에 기록한다.

Day2 상태:
  - REGULATION_BY_DESTINATION: supervisor가 import하는 매핑 딕셔너리 (Day1)
  - generate_embedding(): 텍스트 → Bedrock Cohere Embed v4 벡터 변환 (Day2)
  - search_regulations(): pgvector 코사인 유사도 RAG 검색 (Day2)
  - compliance_node: stage_compliance 전이 + 더미 result 반환 뼈대 (Day1)
  - 실제 judge 함수(Opus 호출)는 Day3에 채운다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.state import BatchState
from backend.infrastructure.trace import trace_node, trace_tool
from backend.llm.embedding_factory import embed_query


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
# 2. RAG 도구 함수 (Day2)
# ---------------------------------------------------------------------------

@trace_tool("generate_embedding")
async def generate_embedding(input_text: str) -> list[float]:
    """
    텍스트 → Bedrock Cohere Embed v4 벡터 변환.
    embedding_factory.embed_query()를 래핑한다.
    - 차원: embedding_factory 테스트 후 schema.sql vector(N) 과 맞출 것
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
      1. query_text 를 1536차원 벡터로 변환 (generate_embedding 호출)
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
          "similarity": 0.92,   # 1.0 - 코사인 거리 (높을수록 관련성 높음)
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

    # pgvector <=> 연산자: 코사인 거리 (0에 가까울수록 유사)
    # ::vector 캐스팅으로 Python list → pgvector 타입 변환
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
                "query_vector": str(query_vector),   # list → 문자열, psycopg가 vector로 변환
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
# 3. compliance_node — Day1 뼈대 (LLM 호출 없음)
#
#    오늘은 stage 전이와 더미 result 반환만 한다.
#    실제 규제 judge(Opus + RAG) 는 Day3에 이 함수 안을 채운다.
#    stage_compliance
#    @trace_node 는 상태를 "변경"하는 함수 위에 붙이는 데코레이터다.
#    db, batch_id 인자가 있을 때 audit_trail에 자동 기록된다.
#    (오늘은 db 인자 없이 state만 받으므로 audit_trail 기록은 Day3에 완성)
# ---------------------------------------------------------------------------

@trace_node("compliance", "agent")
async def compliance_node(state: BatchState) -> BatchState:
    """
    Compliance Interpreter 노드 — Day1 뼈대

    수신: stage_geo 완료 후의 BatchState
    처리:
      - (Day1) 더미 verdicts 딕셔너리로 compliance_result 채움
      - current_stage를 "stage_compliance"로 전이
    반환: 갱신된 BatchState (이후 supervisor → risk_scoring 으로 라우팅)
    """

    # 적용 대상 규제 목록 확인 (supervisor의 route()가 최초 1회 주입)
    applicable: list[str] = state.get("applicable_regulations") or []

    # ------------------------------------------------------------------
    # Day1: LLM 판정 없이 더미 result만 채운다.
    #   형태는 Day3 stage_compliance실판정 결과와 동일한 구조로 잡아두어
    #   supervisor/downstream 코드가 키 구조에 의존해도 안전하도록 함.
    # ------------------------------------------------------------------
    dummy_verdicts: dict[str, Any] = {
        reg_code: {
            "verdict": "compliance_passed",       # 더미: 전부 패스
            "needs_human_review": False,
            "cited_clauses": [],
            "confidence_score": 1.0,
            "reasoning_text": "Day1 skeleton — judge not yet implemented",
        }
        for reg_code in applicable
    }

    compliance_result: dict[str, Any] = {
        "verdicts": dummy_verdicts,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "is_skeleton": True,   # Day3에 이 키 제거 예정
    }

    # BatchState 갱신 — 스프레드로 불변성 유지
    return {
        **state,
        "current_stage": "stage_compliance",   # schema.sql chk_batch_stage 값
        "compliance_result": compliance_result,
        # confidence_score는 Day3 judge 결과 기반으로 재산정.
        # 오늘은 기존 값 그대로 유지(변경 안 함).
    }
