"""
handlers/batch_trigger.py — A1: 배치 생성 + graph 트리거

SubmissionApproved / SubmissionCompleted 이벤트를 수신해
DB에서 batch의 product_id · destination을 조회한 뒤 LangGraph 파이프라인을 시작한다.

발행 순서 규칙 (spec 준수):
  event_bus dispatch → on_submission_approved(payload)
  → DB 조회 (batch product_id / destination)
  → graph.start_graph() → ainvoke(initial_state, thread_id=batch_id)

이중 invoke 방지: current_stage != 'stage_queued' 이면 이미 파이프라인이 기동된 것이므로 스킵.
  (pipeline_worker.py 의 queue 경로와 중복 실행될 수 있어 방어 필요)

SubmissionApproved 는 현재 batch_id=None 으로 발행되므로 graceful skip.
"""
import logging
from sqlalchemy import text
from backend.agents.graph import start_graph
from backend.infrastructure.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def on_submission_approved(payload: dict) -> None:
    """
    SubmissionApproved / SubmissionCompleted 공통 핸들러.

    payload.batch_id 가 없으면 스킵.
    배치가 이미 stage_queued 를 벗어났으면 이중 invoke 방지를 위해 스킵.
    """
    batch_id: str | None = payload.get("batch_id")
    if not batch_id:
        logger.warning("[batch_trigger] %s: batch_id 없음 — 스킵", payload.get("event_name", "unknown"))
        return

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text(
                "SELECT product_id, destination, current_stage "
                "FROM batches WHERE batch_id = :bid"
            ),
            {"bid": batch_id},
        )).mappings().fetchone()

    if row is None:
        logger.warning("[batch_trigger] 배치 %s 없음 — 스킵", batch_id)
        return

    product_id = str(row["product_id"]) if row["product_id"] else None
    destination = row["destination"]

    if not product_id or not destination:
        logger.warning("[batch_trigger] 배치 %s: product_id/destination 누락 — 스킵", batch_id)
        return

    if row["current_stage"] != "stage_queued":
        logger.info(
            "[batch_trigger] 배치 %s: 이미 %s 단계 — 이중 invoke 스킵",
            batch_id, row["current_stage"],
        )
        return

    confirmed_fields: dict = payload.get("confirmed_fields") or {}
    logger.info("[batch_trigger] 그래프 시작 batch=%s product=%s dest=%s", batch_id, product_id, destination)
    await start_graph(batch_id, product_id, destination, confirmed_fields=confirmed_fields)
