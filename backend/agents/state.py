"""
agents/state.py  (담당: 팀원 B — BatchState 공유)

LangGraph 5인 에이전트 파이프라인의 공통 State 구조체.
모든 에이전트가 공유하는 단일 진실 공급원.

스키마 매핑: batches 테이블의 batch_id/product_id/destination/current_stage/
confidence_score 컬럼과 1:1 대응. status는 LangGraph 내부에서 current_stage로
표현되므로 별도 필드를 두지 않음.

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
    # batches.current_stage — Supervisor 라우팅 키
    current_stage: Literal[
        "queued", "extraction", "verification",
        "geo_analysis", "compliance", "readiness",
        "hitl_wait", "completed",
    ]
    # batches.confidence_score — 0.85 미만이면 hitl_interrupt
    confidence_score: float
    # 적용 규제 코드 목록 — regulations.regulation_code 참조 (예: ["UFLPA","IRA","EU_BATTERY_ART47"])
    applicable_regulations: List[str]
    # HITL interrupt 발동 여부 — hitl_interrupt_node에서 True로 전이
    hitl_required: bool
    # HITL 정지 사유 — "low_confidence" | "gray_zone" | None
    hitl_reason: Optional[str]
    # DPP 발행 준비도 점수 (0.0~1.0) — Automation Agent가 산출
    readiness_score: float
    # ----- 도메인 노드 결과 누적 (각 노드가 자기 키에만 기록) -----
    # geo_audit_node 결과 (스펙 5-2)
    geo_result: dict
    # compliance_node 결과 (스펙 4장)
    compliance_result: dict