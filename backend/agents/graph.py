from dataclasses import asdict
from inspect import isawaitable, signature
import logging
from uuid import UUID

logger = logging.getLogger(__name__)

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from langgraph.types import Command, interrupt
from sqlalchemy import select, text

from backend.agents.automation import (
    run_feoc_verification,
    run_risk_scoring,
    run_readiness,
    run_issuance,
)
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
from backend.infrastructure.queue import enqueue, NOTIFICATION_QUEUE
from backend.infrastructure.trace import trace_node
from backend.core.config import config
from backend.domains.dpp.models import Batch


def supervisor_node(state: BatchState) -> BatchState:
    return {**state}


def route_batch(state: BatchState) -> str:
    return route(state)


def traced_graph_node(node_name: str, node_func, node_type: str = "agent"):
    @trace_node(node_name=node_name, node_type=node_type)
    async def run_with_audit(state: BatchState, db) -> BatchState:
        params = signature(node_func).parameters
        result = node_func(state, db) if "db" in params else node_func(state)
        if isawaitable(result):
            result = await result
        return result

    async def run(state: BatchState) -> BatchState:
        async with AsyncSessionLocal() as db:
            return await run_with_audit(state, db)

    return run


async def verification_node(state: BatchState, db) -> BatchState:
    batch_id = UUID(state["batch_id"])
    extraction = state.get("extraction_result") or {}
    supplier_ids = [UUID(s) for s in (extraction.get("supplier_ids") or [])]
    if not supplier_ids:
        raise ValueError("extraction_result에 supplier_ids가 누락되었습니다.")
    result = await run_feoc_verification(db=db, batch_id=batch_id, supplier_ids=supplier_ids)
    error_reason = state.get("error_reason")
    hitl_required = bool(state.get("hitl_required"))
    if not result.get("feoc_passed"):
        error_reason = "feoc_violation"
        hitl_required = True
    return {
        **state,
        "verification_result": result,
        "error_reason": error_reason,
        "hitl_required": hitl_required,
        "current_stage": "stage_verification",
    }


async def risk_scoring_node(state: BatchState, db) -> BatchState:
    batch_id = UUID(state["batch_id"])
    extraction = state.get("extraction_result") or {}
    supplier_ids = [UUID(s) for s in (extraction.get("supplier_ids") or [])]
    if not supplier_ids:
        raise ValueError("extraction_result에 supplier_ids가 누락되었습니다.")
    updates = await run_risk_scoring(
        db=db,
        batch_id=batch_id,
        supplier_ids=supplier_ids,
        compliance_result=state.get("compliance_result"),
        geo_result=state.get("geo_result"),
        current_hitl_required=bool(state.get("hitl_required")),
    )
    return {**state, **updates}


async def readiness_node(state: BatchState, db) -> BatchState:
    product_id = UUID(state["product_id"])
    updates = await run_readiness(db=db, product_id=product_id)
    return {**state, **updates}


async def issuance_node(state: BatchState, db) -> BatchState:
    batch_id = UUID(state["batch_id"])
    product_id = UUID(state["product_id"])
    updates = await run_issuance(db=db, batch_id=batch_id, product_id=product_id)
    return {**state, **updates}


def _batch_id(state: BatchState) -> UUID:
    value = state.get("batch_id")
    if value is None:
        raise ValueError("batch_id is required")
    return UUID(value)


async def _resume_batch(batch_id: UUID) -> None:
    async with AsyncSessionLocal() as db:
        await resume_batch_processing(db, batch_id)
        await db.commit()


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
        owner_users = (await db.execute(
            text("""
                SELECT u.user_id FROM batches b
                JOIN users u ON u.tenant_id = b.tenant_id
                WHERE b.batch_id = :bid
                  AND u.role = 'owner_esg'
                  AND u.is_active = TRUE
            """),
            {"bid": str(batch_id)}
        )).fetchall()
        await db.commit()

    if created:
        event = HITLRequestedEvent(batch_id=batch_id, reason=reason)
        await publish(event.event_name, asdict(event))
        if not owner_users:
            logger.warning("[notification] owner_esg 담당자 없음 (batch_id=%s)", batch_id)
        else:
            for row in owner_users:
                uid = str(row[0])
                await enqueue(
                    NOTIFICATION_QUEUE,
                    "process_notification",
                    user_id=uid,
                    channel="in-app",
                    notification_type="approval_needed",
                    subject="HITL 심사 요청이 생성됐습니다",
                    body=f"배치 {batch_id} 검토가 필요합니다.",
                    dedup_key=f"hitl_created:{batch_id}:{uid}",
                )

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
    return {
        **paused_state,
        "batch_status": "batch_processing",
        "confidence_score": max(float(state.get("confidence_score") or 0.0), 0.85),
        "hitl_required": False,
        "error_reason": None,
    }


builder = StateGraph(BatchState)
builder.add_node("supervisor", supervisor_node)
builder.add_node("data_gateway", traced_graph_node("data_gateway", data_gateway_node))
builder.add_node("verification", traced_graph_node("verification", verification_node))
builder.add_node("geo_audit", traced_graph_node("geo_audit", geo_audit_node))
builder.add_node("compliance", traced_graph_node("compliance", compliance_node))
builder.add_node("risk_scoring", traced_graph_node("risk_scoring", risk_scoring_node))
builder.add_node("readiness", traced_graph_node("readiness", readiness_node))
builder.add_node("issuance", traced_graph_node("issuance", issuance_node))
builder.add_node("hitl_interrupt", traced_graph_node("hitl_interrupt", hitl_interrupt_node, "human"))
builder.add_node("completed", supervisor_node)

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
        "hitl_interrupt": "hitl_interrupt",
        "completed": "completed",
    },
)

for node_name in ("data_gateway", "verification", "geo_audit", "compliance", "risk_scoring", "readiness", "issuance"):
    builder.add_edge(node_name, "supervisor")

builder.add_edge("hitl_interrupt", "supervisor")
builder.add_edge("completed", END)

_pool: AsyncConnectionPool | None = None
graph = None  # initialized by setup_graph() at app startup


async def _ensure_graph():
    if graph is None:
        await setup_graph()
    return graph


async def create_batch(db, product_id: str, destination: str) -> str:
    batch = Batch(product_id=UUID(product_id), destination=destination)
    db.add(batch)
    await db.flush()
    await db.commit()
    return str(batch.batch_id)


async def start_graph(
    batch_id: str,
    product_id: str,
    destination: str,
    confirmed_fields: dict | None = None,
) -> None:
    graph_app = await _ensure_graph()
    initial_state = BatchState(
        batch_id=batch_id,
        product_id=product_id,
        destination=destination,
        current_stage="stage_queued",
        batch_status="batch_processing",
        confirmed_fields=confirmed_fields or {},
    )
    config = {"configurable": {"thread_id": batch_id}}
    await graph_app.ainvoke(initial_state, config=config)


# ── HITL resume 접점 ──────────────────────────────────────────────────────────
# thread_id 규칙: str(batch_id). graph 최초 invoke 시에도 동일 규칙을 사용할 것.
#
# 차윤의 POST /hitl/{batch_id}/resolve 가 approve 결정을 내리면 이 함수를 호출한다.
#   from backend.agents.graph import resume_graph
#   await resume_graph(str(batch_id), resolution)
#
# reject 시에는 재개하지 않는다 — batch 상태 처리는 차윤 쪽 hitl.service 가 담당.

async def _load_state_from_db(batch_id: str) -> BatchState:
    async with AsyncSessionLocal() as db:
        row = await db.scalar(select(Batch).where(Batch.batch_id == UUID(batch_id)))
        if row is None:
            raise ValueError(f"batch not found: {batch_id}")
        return BatchState(
            batch_id=str(row.batch_id),
            product_id=str(row.product_id) if row.product_id else None,
            destination=row.destination,
            current_stage=row.current_stage,
            batch_status=row.status,
            confidence_score=float(row.confidence_score) if row.confidence_score else None,
        )


async def setup_graph() -> None:
    global graph, _pool
    if graph is not None:
        return
    conn_string = config.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    # setup() uses CREATE INDEX CONCURRENTLY which requires autocommit.
    # Advisory lock serializes concurrent workers so only one runs migrations.
    async with AsyncPostgresSaver.from_conn_string(conn_string) as tmp:
        await tmp.conn.execute("SELECT pg_advisory_lock(1990614)")
        try:
            await tmp.setup()
        finally:
            await tmp.conn.execute("SELECT pg_advisory_unlock(1990614)")
    _pool = AsyncConnectionPool(conninfo=conn_string, open=False)
    await _pool.open()
    graph = builder.compile(checkpointer=AsyncPostgresSaver(_pool))


async def teardown_graph() -> None:
    global graph, _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
    graph = None


async def resume_graph(batch_id: str, resolution: str) -> None:
    """approve 결정 후 interrupt() 로 멈춘 graph 를 재개한다.

    체크포인트가 없으면 DB state 로 graph 를 새로 invoke 한다.
    """
    if resolution != "approve":
        return
    graph_app = await _ensure_graph()
    config = {"configurable": {"thread_id": batch_id}}
    snapshot = await graph_app.aget_state(config)
    if not snapshot.values:
        state = await _load_state_from_db(batch_id)
        await _resume_batch(UUID(batch_id))
        state = {
            **state,
            "batch_status": "batch_processing",
            "confidence_score": max(float(state.get("confidence_score") or 0.0), 0.85),
            "hitl_required": False,
            "error_reason": None,
        }
        await graph_app.ainvoke(state, config=config)
    else:
        await graph_app.ainvoke(
            Command(resume={"event_name": "HITLApproved"}),
            config=config,
        )
