import logging
from arq.connections import RedisSettings
from backend.core.config import config
from backend.agents.graph import resume_graph

logger = logging.getLogger(__name__)

async def process_hitl_resolution(ctx, batch_id: str, resolution: str):
    """
    [ARQ Worker] 
    HITL 심사관의 결정(Approve)이 큐에 들어오면, 
    중단되었던 LangGraph 파이프라인을 재개(Resume)합니다.
    """
    logger.info(f"[HITL Worker] 파이프라인 재개 요청 수신: batch_id={batch_id}, resolution={resolution}")
    
    # graph.py 에 정의된 resume_graph 함수 호출
    await resume_graph(batch_id, resolution)
    
    logger.info(f"[HITL Worker] 파이프라인 재개 성공: batch_id={batch_id}")

class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(config.REDIS_URL)
    queue_name = "hitl_queue"  # queue.py의 HITL_QUEUE 상수와 동일
    functions = [process_hitl_resolution]
    
    async def on_startup(ctx):
        logger.info("HITL Worker is starting...")