from dataclasses import asdict
from inspect import isawaitable, signature
from uuid import UUID

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from backend.agents.compliance import compliance_node
from backend.agents.data_gateway import data_gateway_node
from backend.agents.geo_audit import geo_audit_node
from backend.agents.state import BatchState
from backend.agents.supervisor import route
from backend.domains.audit import repository
from backend.domains.audit.state_machine import (
    pause_batch_for_review,
    resume_batch_processing,
)
from backend.events.types import HITLRequestedEvent
from backend.infrastructure.database import AsyncSessionLocal
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node


def supervisor_node(state: BatchState) -> BatchState:
    return {**state}


def route_batch(state: BatchState) -> str:
    return route(state)


def placeholder_node(next_stage: str):
    def advance_stage(state: BatchState) -> BatchState:
        return {**state, "current_stage": next_stage}

    return advance_stage


def traced_graph_node(node_name: str, node_func, node_type: str = "agent"):
    @trace_node(node_name=node_name, node_type=node_type)
    async def run_with_audit(state: BatchState, db) -> BatchState:
        params = signature(node_func).parameters
        if "db" in params:
            result = node_func(state, db)
        else:
            result = node_func({**state, "db": db})
        if isawaitable(result):
            result = await result
        clean_result = {**result}
        clean_result.pop("db", None)
        return clean_result

    async def run(state: BatchState) -> BatchState:
        async with AsyncSessionLocal() as db:
            return await run_with_audit(state, db)

    return run


async def data_gateway_graph_node(state: BatchState) -> BatchState:
    if state.get("document_id") is None:
        return {
            **state,
            "current_stage": "stage_extraction",
            "extraction_result": {
                "parsed": False,
                "note": "no document_id in graph smoke path",
            },
        }
    return await data_gateway_node(state)


def _batch_id(state: BatchState) -> UUID:
    value = state.get("batch_id")
    if value is None:
        raise ValueError("batch_id is required")
    return UUID(value)


async def _pause_batch(batch_id: UUID) -> None:
    async with AsyncSessionLocal() as db:
        await pause_batch_for_review(db, batch_id)
        await db.commit()


async def _resume_batch(batch_id: UUID) -> None:
    async with AsyncSessionLocal() as db:
        await resume_batch_processing(db, batch_id)
        await db.commit()


@trace_node(node_name="hitl_interrupt", node_type="human")
async def hitl_interrupt_node(state: BatchState) -> BatchState:
    batch_id = _batch_id(state)
    trigger_stage = state["current_stage"]
    reason = (
        "risk_escalated"
        if state.get("error_reason") == "risk_escalated"
        else "gray_zone"
    )
    paused_state: BatchState = {**state, "batch_status": "batch_hitl_wait"}

    async with AsyncSessionLocal() as db:
        await pause_batch_for_review(db, batch_id)
        review_id, created = await repository.create_pending_hitl_review(
            db,
            batch_id=batch_id,
            reason=reason,
            trigger_stage=trigger_stage,
        )
        await db.commit()

    if created:
        event = HITLRequestedEvent(batch_id=batch_id, reason=reason)
        await publish(event.event_name, asdict(event))

    response = interrupt(
        {
            "type": "hitl_review",
            "review_id": str(review_id),
            "batch_id": str(batch_id),
            "reason": reason,
            "trigger_stage": trigger_stage,
            "batch_status": "batch_hitl_wait",
        }
    )
    if not isinstance(response, dict) or response.get("event_name") != "HITLApproved":
        raise ValueError("HITLApproved response is required to resume the batch")

    await _resume_batch(batch_id)
    return {**paused_state, "batch_status": "batch_processing"}


@trace_node(node_name="supplier_reverify", node_type="human")
async def supplier_reverify_node(state: BatchState) -> BatchState:
    batch_id = _batch_id(state)
    trigger_stage = state["current_stage"]
    paused_state: BatchState = {**state, "batch_status": "batch_hitl_wait"}

    await _pause_batch(batch_id)
    interrupt(
        {
            "type": "supplier_reverify",
            "batch_id": str(batch_id),
            "reason": "low_confidence",
            "trigger_stage": trigger_stage,
            "batch_status": "batch_hitl_wait",
        }
    )

    await _resume_batch(batch_id)
    return {**paused_state, "batch_status": "batch_processing"}


builder = StateGraph(BatchState)
builder.add_node("supervisor", supervisor_node)
builder.add_node("data_gateway", traced_graph_node("data_gateway", data_gateway_graph_node))
builder.add_node("verification", traced_graph_node("verification", placeholder_node("stage_verification")))
builder.add_node("geo_audit", traced_graph_node("geo_audit", geo_audit_node))
builder.add_node("compliance", traced_graph_node("compliance", compliance_node))
builder.add_node("risk_scoring", traced_graph_node("risk_scoring", placeholder_node("stage_risk")))
builder.add_node("readiness", traced_graph_node("readiness", placeholder_node("stage_readiness")))
builder.add_node("issuance", traced_graph_node("issuance", placeholder_node("stage_issuance")))
builder.add_node("supplier_reverify", traced_graph_node("supplier_reverify", supplier_reverify_node, "human"))
builder.add_node("hitl_interrupt", traced_graph_node("hitl_interrupt", hitl_interrupt_node, "human"))
builder.add_node("completed", traced_graph_node("completed", supervisor_node))

builder.set_entry_point("supervisor")
builder.add_conditional_edges(
    "supervisor",
    route_batch,
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
    builder.add_edge(node_name, "supervisor")

builder.add_edge("supplier_reverify", END)
builder.add_edge("hitl_interrupt", END)
builder.add_edge("completed", END)

# Development checkpoint storage. Production should replace this with a durable saver.
graph = builder.compile(checkpointer=InMemorySaver())
