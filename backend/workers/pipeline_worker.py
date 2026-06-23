import os
from typing import Any, Dict

from arq.connections import RedisSettings


async def start_batch_pipeline(
    ctx: Dict[str, Any],
    batch_id: str,
    product_id: str,
    destination: str,
) -> str:
    from backend.agents.graph import start_graph
    await start_graph(batch_id, product_id, destination)
    return f"파이프라인 시작 완료 (batch: {batch_id})"


class WorkerSettings:
    functions = [start_batch_pipeline]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    queue_name = "batch_pipeline_queue"
    max_tries = 3
