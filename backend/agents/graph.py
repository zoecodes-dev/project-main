from backend.agents.state import BatchState
from backend.agents.supervisor import route
from langgraph.graph import END, StateGraph


def supervisor_node(state: BatchState) -> BatchState:
    return {**state}


def placeholder_node(next_stage: str):
    def advance_stage(state: BatchState) -> BatchState:
        return {**state, "current_stage": next_stage}

    return advance_stage


def stop_placeholder_node(state: BatchState) -> BatchState:
    return {**state}


graph = StateGraph(BatchState)
graph.add_node("supervisor", supervisor_node)
graph.add_node("data_gateway", placeholder_node("stage_extraction"))
graph.add_node("verification", placeholder_node("stage_verification"))
graph.add_node("geo_audit", placeholder_node("stage_geo"))
graph.add_node("compliance", placeholder_node("stage_compliance"))
graph.add_node("risk_scoring", placeholder_node("stage_risk"))
graph.add_node("readiness", placeholder_node("stage_readiness"))
graph.add_node("issuance", placeholder_node("stage_issuance"))
graph.add_node("supplier_reverify", stop_placeholder_node)
graph.add_node("hitl_interrupt", stop_placeholder_node)
graph.add_node("completed", stop_placeholder_node)

graph.set_entry_point("supervisor")
graph.add_conditional_edges(
    "supervisor",
    route,
    {
        "data_gateway": "data_gateway",
        "verification": "verification",
        "geo_audit": "geo_audit",
        "compliance": "compliance",
        "risk_scoring": "risk_scoring",
        "readiness": "readiness",
        "issuance": "issuance",
        "supplier_reverify": "supplier_reverify",
        "hitl_interrupt": "hitl_interrupt",
        "completed": "completed",
    },
)

for node_name in (
    "data_gateway",
    "verification",
    "geo_audit",
    "compliance",
    "risk_scoring",
    "readiness",
    "issuance",
):
    graph.add_edge(node_name, "supervisor")

graph.add_edge("supplier_reverify", END)
graph.add_edge("hitl_interrupt", END)
graph.add_edge("completed", END)
