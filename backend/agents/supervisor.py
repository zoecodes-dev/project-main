from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agents.state import BatchState


def route(state: "BatchState") -> str:
    """
    BatchState 기준으로 다음 노드 이름을 반환한다.
    LLM 호출 없이 schema.sql의 current_stage / batch_status 어휘를 따르는
    결정론적 Pipeline Coordinator 라우터다.
    """
    if state.get("applicable_regulations") is None:
        try:
            from backend.agents.compliance import REGULATION_BY_DESTINATION
        except ImportError:
            # 규제 매핑을 못 불러오면 무검사 통과가 되므로 절대 폴백하지 않는다.
            raise

        destination = state.get("destination")
        state["applicable_regulations"] = REGULATION_BY_DESTINATION.get(destination, [])

    confidence = state.get("confidence_score")
    if confidence is not None and confidence < 0.85:
        if state.get("error_reason") == "low_confidence":
            return "supplier_reverify"
        return "hitl_interrupt"

    current_stage = state.get("current_stage")
    if current_stage == "stage_queued":
        return "data_gateway"
    if current_stage == "stage_extraction":
        return "verification"
    if current_stage == "stage_verification":
        return "geo_audit"
    if current_stage == "stage_geo":
        return "compliance"
    if current_stage == "stage_compliance":
        return "risk_scoring"
    if current_stage == "stage_risk":
        return "readiness"
    if current_stage == "stage_readiness":
        return "issuance"
    if current_stage == "stage_issuance":
        return "completed"

    return "completed"
