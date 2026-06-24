"""
agents/compliance.py — Compliance Interpreter Agent (은지 / C)

역할:
  BatchState의 verification_result를 받아, 목적지(destination)별로 적용되는
  규제 각각의 준수 여부를 판정하고 compliance_results 테이블에 기록한다.

==========================================================================
W6 수요일 리팩토링 (은지 — D5 + H2)
==========================================================================

  ■ D5: HITL 트리거 일원화 (confidence 단일 출처)
  ──────────────────────────────────────────────
    【변경 전 — 문제점】
      compliance_node() 안에 `0.84`라는 매직 넘버가 하드코딩되어 있었다.
      "왜 0.84인가?" → supervisor의 route()가 confidence < 0.85이면
      hitl_interrupt 노드로 보내기 때문. 0.84는 그 임계치 바로 아래 값이다.
      하지만 이 숫자가 코드 곳곳에 흩어지면, 나중에 임계치를 바꿀 때
      한 곳만 고치고 다른 곳을 놓쳐 버그가 난다.

    【변경 후 — 해결】
      1) _HITL_CONFIDENCE_THRESHOLD = 0.85  (supervisor가 HITL로 보내는 기준선)
      2) _HITL_DOWNGRADE_SCORE = 0.84       (기준선 바로 아래 — HITL 강제 트리거용)
      이 두 상수를 compliance.py 모듈 상단에 선언하고,
      _compute_hitl_confidence() 헬퍼 함수 한 곳에서만 점수를 계산한다.
      → "HITL을 트리거하는 로직은 오직 compliance 노드에서만 관리" 라는
        단일 출처(Single Source of Truth) 원칙이 확립된다.

    【다른 파일과의 관계 — 경계 규칙 준수】
      - graph.py의 hitl_interrupt_node에도 confidence 관련 코드가 있다:
          confidence_score: max(..., 0.85)
        이것은 "HITL 승인 후 복원"하는 로직이지, "트리거"하는 로직이 아니다.
        graph.py는 A(지혜) 담당이므로 내(C)가 건드리지 않는다.
      - supervisor.py의 route()에서 confidence < 0.85 → hitl_interrupt 분기.
        이것도 A(지혜) 담당이므로 건드리지 않는다.
      - 즉 내 책임 범위는: "언제 confidence를 낮출 것인가"만 관리하는 것.

  ■ H2: LLM 경로 통일 (Bedrock 일원화)
  ──────────────────────────────────────
    【변경 전 — 문제점】
      _call_sonnet_for_verdict()가 httpx로 Anthropic REST API를 직접 호출했다.
      - API 키를 settings.ANTHROPIC_API_KEY에서 직접 가져옴
      - 모델 ID를 _SONNET_MODEL 상수로 별도 관리
      - 인증 방식이 다른 에이전트들(Bedrock IAM Role)과 달라 관리 포인트 2개

    【변경 후 — 해결】
      B(은진)이 만든 공통 팩토리 `bedrock_factory.py`의
      `get_llm_for_agent("compliance")`를 사용한다.
      - API 키 관리: IAM Role이 자동 처리 (코드에 키 없음)
      - 모델 선택: AGENT_MODEL_MAP["compliance"] = Model.SONNET_46 (한 곳에서 관리)
      - LangChain ChatBedrockConverse 인스턴스를 반환받아 .ainvoke()로 호출

      결과적으로:
      ① httpx 의존성 제거
      ② _SONNET_MODEL 상수 제거
      ③ _get_anthropic_key() 함수 제거
      ④ _call_sonnet_for_verdict()의 HTTP 호출 → LangChain .ainvoke() 교체

==========================================================================

기존 히스토리 (변경 없는 부분):
  - REGULATION_BY_DESTINATION: supervisor가 import하는 매핑 딕셔너리 (Day1)
  - generate_embedding(): 텍스트 → Bedrock Cohere Embed v4 벡터 변환 (Day2)
  - search_regulations(): pgvector 코사인 유사도 RAG 검색 (Day2)
  - ComplianceCompleted: 이벤트 dataclass (Day3)
  - judge_uflpa/ira/carbon_footprint/recycled_content/generic: 규제별 judge
  - _stub_passed_judge(): CBAM / CONFLICT_MINERALS / CRMA 깡통 judge
  - _insert_compliance_result(): compliance_results INSERT 헬퍼
  - _build_judge_context(): D4 기본값화 적용 완료
  - get_compliance_history_for_batch(): HITL context용 이력 조회
"""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Callable

# ──────────────────────────────────────────────────────────────────────────
# [H2 변경] httpx 제거 → LangChain 메시지 타입 import
#
#   변경 전: import httpx  (Anthropic REST API 직접 호출용)
#   변경 후: langchain_core.messages 에서 SystemMessage, HumanMessage import
#            → get_llm_for_agent()가 반환하는 ChatBedrockConverse 인스턴스에
#              이 메시지 객체들을 넘겨 .ainvoke()로 호출한다.
#
#   왜 LangChain 메시지 타입을 쓰나?
#     ChatBedrockConverse는 LangChain의 BaseChatModel을 상속한다.
#     LangChain 챗 모델은 .invoke([메시지들]) 형태로 호출하는데,
#     이때 메시지는 SystemMessage/HumanMessage/AIMessage 객체여야 한다.
#     일반 문자열이 아니라 "역할이 명시된 메시지 객체"를 넘기는 것.
# ──────────────────────────────────────────────────────────────────────────
from langchain_core.messages import SystemMessage, HumanMessage

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.state import BatchState
from backend.infrastructure.database import AsyncSessionLocal
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_tool
from backend.llm.embedding_factory import embed_query

# ──────────────────────────────────────────────────────────────────────────
# [H2 변경] B(은진)의 공통 LLM 팩토리 import
#
#   bedrock_factory.py에 정의된 get_llm_for_agent("compliance")를 호출하면
#   AGENT_MODEL_MAP에서 "compliance" → Model.SONNET_46 을 찾아
#   ChatBedrockConverse 인스턴스를 반환한다.
#
#   인증은 EC2에 부착된 IAM Role(KIRA-EC2-Bedrock-Role)이 자동 처리하므로
#   API 키를 코드에 넣을 필요가 없다.
#   모델을 바꾸고 싶으면 bedrock_factory.py의 AGENT_MODEL_MAP만 수정하면 된다.
# ──────────────────────────────────────────────────────────────────────────
from backend.llm.bedrock_factory import get_llm_for_agent

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# [D5] HITL 트리거 상수 — 단일 출처 (Single Source of Truth)
# ═══════════════════════════════════════════════════════════════════════════
#
#   이 두 상수가 "사람 검토가 필요한 배치를 HITL로 보내는" 핵심 기준이다.
#
#   동작 원리 (전체 흐름):
#     ① compliance_node가 규제별 judge를 돌린다.
#     ② 하나라도 needs_human_review=True이면
#        → confidence_score를 _HITL_DOWNGRADE_SCORE(0.84)로 낮춘다.
#     ③ compliance_node가 갱신된 state를 반환한다.
#     ④ graph.py의 supervisor_node → route() 가 state를 받는다.
#     ⑤ route()는 confidence < _HITL_CONFIDENCE_THRESHOLD(0.85)이면
#        "hitl_interrupt" 노드로 라우팅한다.
#     ⑥ hitl_interrupt_node에서 사람이 승인하면
#        confidence를 0.85 이상으로 복원하고 다음 단계로 진행한다.
#
#   왜 0.84와 0.85인가?
#     supervisor의 route()가 "< 0.85"로 비교한다.
#     0.84는 0.85 미만이므로 HITL로 빠지고,
#     0.85 이상이면 정상 진행한다.
#     이 1%p 차이가 "자동 통과 vs 사람 검토" 경계선이다.
#
#   변경이 필요할 때:
#     - HITL 진입 기준을 바꾸고 싶으면 → 이 두 상수만 수정
#     - 단, supervisor.py의 route() 임계치(A 담당)와 반드시 동기화해야 함
#       예: THRESHOLD를 0.90으로 올리면, DOWNGRADE는 0.89로 맞춘다.
# ═══════════════════════════════════════════════════════════════════════════

_HITL_CONFIDENCE_THRESHOLD: float = 0.85   # supervisor route()가 HITL로 보내는 기준선
_HITL_DOWNGRADE_SCORE:      float = 0.84   # 기준선 바로 아래 — needs_human_review 시 적용


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
# 4. LLM 판정 호출 래퍼 (Day3 → W6 H2 리팩토링)
#
#    [H2 변경 — 은지 + 은진]
#    RAG로 가져온 조항 + 협력사 데이터를 LLM에게 주고 JSON 판정을 받는다.
#
#    ■ 변경 전 (httpx 직접 호출):
#      async with httpx.AsyncClient() as client:
#          resp = await client.post(
#              "https://api.anthropic.com/v1/messages",
#              headers={"x-api-key": _get_anthropic_key(), ...},
#              json={"model": _SONNET_MODEL, ...},
#          )
#      → API 키를 settings에서 직접 가져오고, 모델 ID도 여기서 관리
#      → 다른 에이전트들은 Bedrock IAM Role 인증인데, compliance만 달랐음
#
#    ■ 변경 후 (Bedrock 경유 LangChain 호출):
#      llm = get_llm_for_agent("compliance")    # ← B(은진) 공통 팩토리
#      response = await llm.ainvoke([SystemMessage(...), HumanMessage(...)])
#      → 인증: IAM Role 자동 (코드에 키 없음)
#      → 모델: bedrock_factory.py의 AGENT_MODEL_MAP에서 한 곳 관리
#      → 호출: LangChain의 표준 인터페이스 (.ainvoke)
#
#    cited_clauses 가 비어 있으면 호출부(judge_*)에서 compliance_reject 처리.
# ---------------------------------------------------------------------------

# ──────────────────────────────────────────────────────────────────────────
# [H2 제거 항목들]
#
#   아래 3개는 더 이상 필요 없어서 삭제했다:
#
#   ❌ _SONNET_MODEL = "global.anthropic.claude-sonnet-4-6"
#      → bedrock_factory.py의 AGENT_MODEL_MAP["compliance"]이 관리
#
#   ❌ def _get_anthropic_key():
#      → IAM Role이 인증 자동 처리, API 키 불필요
#
#   ❌ import httpx
#      → LangChain .ainvoke()로 대체, HTTP 클라이언트 불필요
# ──────────────────────────────────────────────────────────────────────────


async def _call_llm_for_verdict(
    regulation_code: str,
    clauses: list[dict],
    context: dict,
) -> dict:
    """
    [H2] Bedrock 경유 LLM 판정 호출 — _call_sonnet_for_verdict()의 후속 버전.

    함수명이 _call_sonnet_for_verdict → _call_llm_for_verdict로 바뀐 이유:
      더 이상 Sonnet만 쓰는 게 아니라, bedrock_factory의 매핑에 따라
      어떤 모델이든 쓸 수 있기 때문이다 (현재는 Sonnet 4.6이지만 변경 가능).

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

    호출 흐름 (초보자용 단계별 설명):
      1. get_llm_for_agent("compliance")로 LLM 인스턴스를 가져온다.
         → bedrock_factory.py에서 캐시된 ChatBedrockConverse 객체 반환
      2. SystemMessage + HumanMessage 리스트를 만든다.
         → SystemMessage: LLM의 "역할/규칙" 설정 (시스템 프롬프트)
         → HumanMessage: 실제 판정 요청 내용 (사용자 프롬프트)
      3. llm.ainvoke(messages)로 비동기 호출한다.
         → .ainvoke()는 LangChain의 비동기 호출 메서드
         → 내부적으로 Bedrock Converse API를 호출
      4. 응답에서 .content를 추출해 JSON 파싱한다.
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

    # ──────────────────────────────────────────────────────────────────────
    # [H2 핵심 변경] httpx 직접 호출 → LangChain .ainvoke()
    #
    #   변경 전:
    #     async with httpx.AsyncClient() as client:
    #         resp = await client.post("https://api.anthropic.com/v1/messages", ...)
    #     raw = resp.json()["content"][0]["text"]
    #
    #   변경 후:
    #     llm = get_llm_for_agent("compliance")
    #     response = await llm.ainvoke([SystemMessage(...), HumanMessage(...)])
    #     raw = response.content
    #
    #   차이점:
    #     - get_llm_for_agent는 lru_cache로 캐시됨 → 매번 새 인스턴스 안 만듦
    #     - .ainvoke()는 LangChain의 표준 비동기 호출 → 어떤 백엔드든 동일 코드
    #     - response.content는 문자열 (LangChain AIMessage의 .content 속성)
    #     - API 키/인증: IAM Role이 boto3 내부에서 자동 처리
    # ──────────────────────────────────────────────────────────────────────
    llm = get_llm_for_agent("compliance", max_tokens=1024)

    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])

    # LangChain AIMessage의 .content 속성에서 텍스트 추출
    raw = response.content.strip()

    # LLM이 ```json 블록으로 감싸는 경우 방어 (모델 무관하게 발생 가능)
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
            "[cited_clauses 누락] LLM이 근거 조항을 제시하지 못했어요. "
            + result.get("reasoning_text", "")
        )
    result.setdefault("needs_human_review", False)
    result.setdefault("confidence_score", 0.9)
    return result


# ---------------------------------------------------------------------------
# 5. Stub judge — CBAM / CONFLICT_MINERALS / CRMA (Day3)
#    항상 compliance_passed 반환. LLM 호출 없음.
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
#
#    [H2 변경 사항]
#    모든 judge 함수의 except 절에서:
#      변경 전: except (httpx.HTTPError, json.JSONDecodeError, KeyError)
#      변경 후: except (Exception,)
#      이유: httpx를 더 이상 쓰지 않으므로 httpx.HTTPError가 발생하지 않는다.
#            대신 Bedrock/LangChain 관련 예외(botocore ClientError 등)를
#            포괄적으로 잡아 compliance_reject 처리한다.
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
        result = await _call_llm_for_verdict("UFLPA", clauses, context)
    except (json.JSONDecodeError, KeyError, Exception) as exc:
        return {
            "verdict": "compliance_reject",
            "needs_human_review": True,
            "cited_clauses": [],
            "confidence_score": 0.0,
            "reasoning_text": f"LLM 호출 실패: {exc}",
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
        result = await _call_llm_for_verdict("IRA", clauses, context)
    except (json.JSONDecodeError, KeyError, Exception) as exc:
        return {
            "verdict": "compliance_reject",
            "needs_human_review": True,
            "cited_clauses": [],
            "confidence_score": 0.0,
            "reasoning_text": f"LLM 호출 실패: {exc}",
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
      3. 가중평균값을 임계치와 비교해 LLM에 힌트로 제공 후 판정.

    판정 기준 (EU 2023/1542 Annex II):
      - weighted_carbon_intensity > 100 kgCO2eq/kWh → compliance_violation
      - weighted_carbon_intensity >  75 kgCO2eq/kWh → compliance_warning
      - weighted_carbon_intensity <=  75             → compliance_passed
      - 선언 데이터 전혀 없음                         → compliance_reject + needs_human_review
      - 선언 누락 공장 존재                           → 판정 후 needs_human_review=True 강제
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

    # LLM 컨텍스트 구성
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
        result = await _call_llm_for_verdict("EU_BATTERY_ART7", clauses, enriched_context)
    except (json.JSONDecodeError, KeyError, Exception) as exc:
        return {
            "verdict":            "compliance_reject",
            "needs_human_review": True,
            "cited_clauses":      [],
            "confidence_score":   0.0,
            "reasoning_text":     f"LLM 호출 실패: {exc}",
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

    # 광물별 임계치 위반 사전 계산 — LLM 힌트로 제공
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
        result = await _call_llm_for_verdict("EU_BATTERY", clauses, enriched_context)
    except (json.JSONDecodeError, KeyError, Exception) as exc:
        return {
            "verdict":            "compliance_reject",
            "needs_human_review": True,
            "cited_clauses":      [],
            "confidence_score":   0.0,
            "reasoning_text":     f"LLM 호출 실패: {exc}",
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
        result = await _call_llm_for_verdict(regulation_code, clauses, context)
    except (json.JSONDecodeError, KeyError, Exception) as exc:
        return {
            "verdict": "compliance_reject",
            "needs_human_review": True,
            "cited_clauses": [],
            "confidence_score": 0.0,
            "reasoning_text": f"LLM 호출 실패: {exc}",
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
    "EU_BATTERY":       judge_recycled_content,
    "EU_BATTERY_ART7":  judge_carbon_footprint,
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
# 9. judge context 빌더 (Day3 + Day2 키 추가 + D4 기본값화)
#
#    [D4 수정 — 은지]
#    geo_audit·verification 노드가 그래프에서 제거될 예정(D2+D3).
#    → state에 geo_result / verification_result 키 자체가 없을 수 있다.
#    → 명시적 기본값(None / [] )을 선제 적용하여
#      KeyError·NoneType 에러를 방어한다.
#
#    변경점:
#      - mine_coordinates  : geo.get(...) → 명시적 None 기본값
#      - geo_risk_flags    : geo.get(...) → 명시적 [] 기본값
#      - verification_flags: verification.get(...) → 명시적 [] 기본값
#      - 주석에 D4 적용 근거 명시
# ---------------------------------------------------------------------------

def _build_judge_context(state) -> dict:
    """
    앞 단계(extraction, verification, geo) 결과를 합쳐
    judge에게 넘길 컨텍스트 dict를 구성한다.

    [D4 수정 사항]
      geo_audit·verification 노드가 D2+D3 그래프 축소로 제거되면
      state에 'geo_result'·'verification_result' 키가 존재하지 않는다.
      기존에도 .get()으로 방어하고 있었지만, 기본값을 명시화하고
      주석으로 근거를 남겨 향후 혼란을 방지한다.
    """
    extraction:   dict = state.get("extraction_result")   or {}

    # [D4] geo/verification 결과는 그래프 축소 후 state에 없을 수 있다.
    #       or {}로 빈 dict 폴백 → .get()에서 None/[] 반환.
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

        # ── [D4] 그래프 축소 방어 — 명시적 기본값 ──
        # geo_audit 노드 제거 후: state에 geo_result 자체가 없음.
        # verification 노드 제거 후: state에 verification_result 자체가 없음.
        # None / [] 기본값으로 judge 함수들이 안전하게 동작하도록 보장.
        "mine_coordinates":        geo.get("mine_coordinates", None),
        "geo_risk_flags":          geo.get("risk_flags", []),
        "verification_flags":      verification.get("flags", []),

        # ── Day2 신규 키 ──
        "recycled_content_ratio":  extraction.get("recycled_content_ratio"),
        "recycled_materials":      extraction.get("recycled_materials"),
    }


# ---------------------------------------------------------------------------
# 9-A. [D5 신규] HITL confidence 계산 — 단일 출처 함수
# ---------------------------------------------------------------------------

def _compute_hitl_confidence(
    any_human_review: bool,
    current_confidence: float | None,
) -> float:
    """
    [D5] HITL 트리거 여부를 결정하는 confidence 점수 계산.

    ┌─────────────────────────────────────────────────────────────┐
    │  이 함수가 "사람 검토가 필요한가?"를 결정하는 유일한 장소다.  │
    │  프로젝트 어디에서도 confidence를 직접 0.84로 하드코딩하지    │
    │  않는다. 반드시 이 함수를 통해 계산한다.                     │
    └─────────────────────────────────────────────────────────────┘

    매개변수:
      any_human_review: 규제별 judge 함수들 중 하나라도
                        needs_human_review=True를 반환했는지 여부
      current_confidence: state에 이미 있던 confidence_score (없으면 None)

    반환:
      - any_human_review=True  → _HITL_DOWNGRADE_SCORE (0.84)
        → supervisor의 route()에서 < 0.85 조건에 걸려 hitl_interrupt로 분기
      - any_human_review=False → 기존 confidence 유지 (없으면 1.0)

    왜 별도 함수로 분리했나?
      1. 검색 가능성: "HITL 트리거 로직 어디 있지?" → 이 함수 하나만 찾으면 됨
      2. 테스트 용이성: 이 함수만 단위 테스트하면 HITL 분기 로직 전체 검증 가능
      3. 변경 안전성: 임계치를 바꿀 때 상수 2개 + 이 함수만 확인하면 됨
    """
    if any_human_review:
        # needs_human_review가 하나라도 있으면 → HITL로 보낸다.
        # _HITL_DOWNGRADE_SCORE(0.84) < _HITL_CONFIDENCE_THRESHOLD(0.85) 이므로
        # supervisor의 route()가 "hitl_interrupt"를 반환한다.
        return _HITL_DOWNGRADE_SCORE

    # 모든 judge가 needs_human_review=False → 기존 confidence 유지
    return float(current_confidence or 1.0)


# ---------------------------------------------------------------------------
# 10. compliance_node — 실판정 버전 (Day3 → W6 D5 리팩토링)
#
#     @trace_node 제거 — graph.py 래퍼(traced_graph_node)가 기록 담당.
#     graph.py 패턴: state 하나만 인자로 받음.
#     DB 세션은 내부에서 AsyncSessionLocal로 직접 연다.
#
#     [D5 변경 사항]
#     변경 전:
#       new_confidence = 0.84 if any_human_review else float(...)
#       ↑ 매직 넘버 0.84가 여기에 직접 하드코딩
#
#     변경 후:
#       new_confidence = _compute_hitl_confidence(any_human_review, state.get(...))
#       ↑ 상수 + 헬퍼 함수로 위임 → 단일 출처 확립
# ---------------------------------------------------------------------------

async def compliance_node(state: BatchState) -> BatchState:
    """
    Compliance Interpreter 노드 — Day3 실판정 버전 (W6 D5 리팩토링)

    수신: data_gateway 완료 후의 BatchState
    처리:
      1. REGULATION_JUDGES 딕셔너리로 규제별 judge 함수를 선택해 호출
      2. 결과를 compliance_results에 INSERT
      3. [D5] _compute_hitl_confidence()로 confidence 계산
         → needs_human_review가 있으면 자동으로 HITL 트리거
      4. ComplianceCompleted 이벤트 발행 → 차윤(E) Readiness 재계산
    반환: 갱신된 BatchState (이후 supervisor → completed 또는 hitl_interrupt)
    """
    batch_id:   str       = state["batch_id"]
    applicable: list[str] = state.get("applicable_regulations") or []

    # KR 또는 빈 목록 → 즉시 패스 (DB·LLM 호출 없음)
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

    # ──────────────────────────────────────────────────────────────────────
    # [D5] HITL confidence 계산 — 단일 출처 함수로 위임
    #
    #   변경 전 (매직 넘버 하드코딩):
    #     new_confidence = 0.84 if any_human_review else float(state.get("confidence_score") or 1.0)
    #
    #   변경 후 (헬퍼 함수 위임):
    #     new_confidence = _compute_hitl_confidence(any_human_review, state.get("confidence_score"))
    #
    #   이 한 줄이 D5의 핵심이다:
    #   - "0.84를 왜 쓰는 거지?" → _compute_hitl_confidence 함수 보면 됨
    #   - "임계치 바꾸고 싶어" → _HITL_CONFIDENCE_THRESHOLD, _HITL_DOWNGRADE_SCORE 수정
    #   - "HITL 트리거 로직 어디 있어?" → compliance.py의 이 함수 하나
    # ──────────────────────────────────────────────────────────────────────
    new_confidence: float = _compute_hitl_confidence(
        any_human_review=any_human_review,
        current_confidence=state.get("confidence_score"),
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
