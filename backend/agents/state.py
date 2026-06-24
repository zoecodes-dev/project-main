"""
agents/state.py  (담당: 팀원 B — BatchState 공유)

LangGraph 5인 에이전트 파이프라인의 공통 State 구조체.
모든 에이전트가 공유하는 단일 진실 공급원이며, schema.sql batches 테이블과 1:1 정렬.

[정합성 핵심 — 두 축은 schema.sql 표기 그대로, 접두어 포함]
  current_stage : 노드 위치 축. schema.sql chk_batch_stage 8종과 1:1
      stage_queued → stage_extraction → stage_verification → stage_geo →
      stage_compliance → stage_risk → stage_readiness → stage_issuance
  batch_status  : 거친 국면 축. schema.sql chk_batch_status 4종과 1:1
      batch_processing / batch_hitl_wait / batch_completed / batch_rejected
      ※ HITL 대기는 current_stage가 아니라 이 축에서 'batch_hitl_wait'로 표현된다.
        interrupt() 시 batch_status='batch_hitl_wait'(current_stage는 중단 지점 유지),
        resume 시 batch_status='batch_processing'으로 복귀해 중단 stage부터 진행.

  Supervisor route()는 두 값을 함께 본다(supervisor.py).

[필드명 주의]
  과거 'status' / 'hitl_reason' / 바닐라 stage값('queued','extraction',
  'geo_analysis'...)으로 작성돼 있었으나, schema.sql·spec 0-5절 기준
  batch_status / error_reason / 접두어 stage값으로 통일한다.
  (특히 'geo_analysis'는 존재하지 않는 stage였다 → 'stage_geo'가 정답.)

total=False: LangGraph 노드가 {**state, "key": ...}로 부분 갱신하는 패턴을
허용하기 위함. 초기 invoke 시 모든 필드를 채우지 않아도 타입체커가 통과.
"""
from typing import List, Literal, Optional, TypedDict


class BatchState(TypedDict, total=False):
    # batches.batch_id (UUID) — 문자열로 직렬화하여 보유
    batch_id: str
    # batches.product_id (UUID)
    product_id: str
    # batches.destination — 허용값: US / EU / KR
    destination: str

    # batches.current_stage — 노드 위치 축 (8종, schema.sql chk_batch_stage와 1:1)
    current_stage: Literal[
        "stage_queued", "stage_extraction", "stage_verification",
        "stage_geo", "stage_compliance", "stage_risk",
        "stage_readiness", "stage_issuance",
    ]
    # batches.status — 거친 국면 축 (4종, schema.sql chk_batch_status와 1:1)
    batch_status: Literal[
        "batch_processing", "batch_hitl_wait", "batch_completed", "batch_rejected",
    ]

    # batches.confidence_score — 0.85 미만이면 supplier_reverify / hitl_interrupt
    confidence_score: float
    # 적용 규제 코드 목록 — route()가 최초 1회 주입. regulations.regulation_code 참조
    #   (예: ["UFLPA","IRA","EU_BATTERY_ART47"])
    applicable_regulations: List[str]

    # HITL interrupt 발동 여부
    hitl_required: bool
    # 저신뢰/회색지대 사유 — "low_confidence" | "gray_zone" | None
    #   data_gateway 노드가 저신뢰 시 "low_confidence" 세팅 → Supervisor가 supplier_reverify 라우팅
    error_reason: Optional[str]

    # ----- 각 노드 결과 누적 (각 노드가 자기 키에만 기록) -----
    confirmed_fields: Optional[dict]      # batch_trigger → data_gateway: 협력사 AI 파싱 확정값
    extraction_result: Optional[dict]    # data_gateway (B, stage_extraction)
    verification_result: Optional[dict]  # verification (E, stage_verification)
    geo_result: Optional[dict]           # geo_audit (D, stage_geo)
    compliance_result: Optional[dict]    # compliance (C, stage_compliance)
    readiness_score: Optional[float]     # readiness (E, stage_readiness)
