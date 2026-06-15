"""
infrastructure/queue.py  (담당: 팀원 B)

ARQ + Redis. 비동기 작업을 Queue에 넣는 단일 인터페이스만 제공한다.

스펙 1-3 / 5-2 인터페이스:
    async def enqueue(queue_name: str, func_name: str, **kwargs) -> str
    # 반환값: job_id (클라이언트에게 202 Accepted 응답 시 포함)

[정합성 핵심 — 큐 이름은 schema.sql processed_jobs.chk_processed_queue 허용값과 1:1]
    schema.sql의 processed_jobs.queue_name CHECK 제약이 허용하는 값만 사용한다:
        document_parse_queue / verification_queue / risk_queue /
        hitl_queue / notification_queue / dpp_publish_queue /
        batch_pipeline_queue / dead_letter_queue
    (과거 표기였던 'ocr_queue' / 'validation_queue'는 허용값에 없어 processed_jobs
     INSERT 시 CHECK 위반으로 멱등성 기록이 깨졌다. 폴더·큐·도메인·state·이벤트를
     전부 verification으로 통일하는 결정에 따라 verification_queue로 일원화한다.)

Retry: 지수 백오프, 최대 3회. 3회 실패 시 dead_letter_queue.
Idempotency: 각 작업 함수는 동일 인자로 두 번 호출돼도 같은 결과를 내야 한다
            (멱등성 키는 processed_jobs 테이블로 영속화 — E 소관, 큐 계약은 공유).
"""
from typing import Optional

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from backend.core.config import config

# ----- Queue 이름 상수 (schema.sql processed_jobs 허용값과 1:1) -----
DOCUMENT_PARSE_QUEUE = "document_parse_queue"   # 구 ocr_queue (문서 파싱 — 은진 Data Gateway)
VERIFICATION_QUEUE = "verification_queue"       # 구 validation_queue (룰 검증 — E)
RISK_QUEUE = "risk_queue"
HITL_QUEUE = "hitl_queue"
NOTIFICATION_QUEUE = "notification_queue"
DPP_PUBLISH_QUEUE = "dpp_publish_queue"
BATCH_PIPELINE_QUEUE = "batch_pipeline_queue"   # W5-#09 batch 시작 큐 (submit→트리거가 enqueue, #10이 소비)
DEAD_LETTER_QUEUE = "dead_letter_queue"

# enqueue 허용 큐 7종 (dead_letter_queue는 실패 시 시스템이 내부적으로만 사용)
QUEUE_NAMES = {
    DOCUMENT_PARSE_QUEUE,
    VERIFICATION_QUEUE,
    RISK_QUEUE,
    HITL_QUEUE,
    NOTIFICATION_QUEUE,
    DPP_PUBLISH_QUEUE,
    BATCH_PIPELINE_QUEUE,
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

    # 클라이언트가 넘긴 Idempotency Key(예: 이벤트 해시값, 고유 ID)를 ARQ _job_id로 매핑.
    # kwargs에서 멱등성 키를 빼내어 ARQ에 전달한다. 없으면 ARQ가 자동 생성.
    idempotency_key = kwargs.pop("job_id", None)

    # ARQ는 _queue_name으로 큐 분리. job 함수명은 worker에 등록된 이름과 일치해야 함.
    job = await pool.enqueue_job(
        func_name,
        _queue_name=queue_name,
        _job_id=idempotency_key,
        **kwargs,
    )

    # job이 None = 동일 _job_id 작업이 이미 큐에 존재(멱등성 방어 성공).
    job_id = job.job_id if job else (idempotency_key or "duplicate")
    print(f"[ENQUEUED] {func_name} -> {queue_name} (job_id={job_id})")

    return job_id