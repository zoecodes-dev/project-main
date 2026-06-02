from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.trace import trace_node


@trace_node(node_name="pause_batch_for_review", node_type="agent")
async def pause_batch_for_review(db: AsyncSession, batch_id: UUID) -> None:
    result = await db.execute(
        text(
            """
            UPDATE batches
            SET status = 'batch_hitl_wait'
            WHERE batch_id = :batch_id
            RETURNING batch_id
            """
        ),
        {"batch_id": str(batch_id)},
    )
    if result.scalar_one_or_none() is None:
        raise ValueError(f"batch not found: {batch_id}")


@trace_node(node_name="resume_batch_processing", node_type="agent")
async def resume_batch_processing(db: AsyncSession, batch_id: UUID) -> None:
    result = await db.execute(
        text(
            """
            UPDATE batches
            SET status = 'batch_processing'
            WHERE batch_id = :batch_id
            RETURNING batch_id
            """
        ),
        {"batch_id": str(batch_id)},
    )
    if result.scalar_one_or_none() is None:
        raise ValueError(f"batch not found: {batch_id}")
