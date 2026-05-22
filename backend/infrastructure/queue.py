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
from typing import Optional

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from backend.core.config import config

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

_redis_pool: Optional[ArqRedis] = None


async def get_redis_pool() -> ArqRedis:
    """ARQ Redis 풀 싱글톤."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = await create_pool(RedisSettings.from_dsn(config.REDIS_URL))
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
    
    # 클라이언트가 넘긴 Idempotency Key(예: 이벤트 해시값, 고유 ID)를 ARQ _job_id로 매핑
    # kwargs에서 멱등성 키를 빼내어 ARQ에 전달합니다. 없으면 ARQ가 자동 생성합니다.
    idempotency_key = kwargs.pop("job_id", None)
    
    # ARQ는 _queue_name으로 큐 분리. job 함수명은 worker에 등록된 이름과 일치해야 함.
    job = await pool.enqueue_job(
        func_name, 
        _queue_name=queue_name, 
        _job_id=idempotency_key, 
        **kwargs
    )
    
    # job이 None이라는 것은 동일한 _job_id를 가진 작업이 이미 큐에 존재한다는 의미 (멱등성 방어 성공)
    job_id = job.job_id if job else (idempotency_key or "duplicate")
    print(f"[ENQUEUED] {func_name} -> {queue_name} (job_id={job_id})")
    
    return job_id