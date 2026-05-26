from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agents.state import BatchState


def route(state: "BatchState") -> str:
    """
    BatchState 기준으로 다음 노드 이름 반환.
    LLM 호출 없는 조건 분기만. 실제 LangGraph 연결은 W2.

    라우팅 규칙:
      - status == rejected          → end
      - confidence_score < 0.85     → hitl_interrupt
      - status == hitl_wait         → hitl_interrupt
      - 그 외                        → compliance
    """
    confidence = state.get("confidence_score")
    status     = state.get("status")

    if status == "rejected":
        return "end"

    if confidence is not None and confidence < 0.85:
        return "hitl_interrupt"

    if status == "hitl_wait":
        return "hitl_interrupt"

    return "compliance"
