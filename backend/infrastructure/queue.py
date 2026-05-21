"""
infrastructure/queue.py  (담당: 팀원 B)

ARQ + Redis. 비동기 작업을 Queue에 넣는 단일 인터페이스만 제공한다.

스펙 1-3 인터페이스:
    async def enqueue(queue_name: str, func_name: str, **kwargs) -> str
    # 반환값: job_id (클라이언트에게 202 Accepted 응답 시 포함)

Queue 이름 6종: ocr / validation / risk / hitl / notification / dpp_publish
Retry: 지수 백오프, 최대 3회. 3회 실패 시 dead_letter_queue.
Idempotency: 각 작업 함수는 동일 인자로 두 번 호출돼도 같은 결과를 내야 한다.
"""
import os
from typing import Optional

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

# ----- Queue 이름 상수 (스펙 1-3) -----
OCR_QUEUE = "ocr_queue"
VALIDATION_QUEUE = "validation_queue"
RISK_QUEUE = "risk_queue"
HITL_QUEUE = "hitl_queue"
NOTIFICATION_QUEUE = "notification_queue"
DPP_PUBLISH_QUEUE = "dpp_publish_queue"
DEAD_LETTER_QUEUE = "dead_letter_queue"

QUEUE_NAMES = {
    OCR_QUEUE,
    VALIDATION_QUEUE,
    RISK_QUEUE,
    HITL_QUEUE,
    NOTIFICATION_QUEUE,
    DPP_PUBLISH_QUEUE,
}

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

_redis_pool: Optional[ArqRedis] = None


async def get_redis_pool() -> ArqRedis:
    """ARQ Redis 풀 싱글톤."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
    return _redis_pool


async def enqueue(queue_name: str, func_name: str, **kwargs) -> str:
    """
    작업을 지정 Queue에 넣고 job_id를 반환한다.
    queue_name은 QUEUE_NAMES 중 하나여야 한다.
    """
    if queue_name not in QUEUE_NAMES:
        raise ValueError(
            f"알 수 없는 Queue: {queue_name}. 허용: {sorted(QUEUE_NAMES)}"
        )

    pool = await get_redis_pool()
    # ARQ는 _queue_name으로 큐 분리. job 함수명은 worker에 등록된 이름과 일치해야 함.
    job = await pool.enqueue_job(func_name, _queue_name=queue_name, **kwargs)
    job_id = job.job_id if job else "duplicate"
    print(f"[ENQUEUED] {func_name} -> {queue_name} (job_id={job_id})")
    return job_id
