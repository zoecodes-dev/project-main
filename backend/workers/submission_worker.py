import os
import logging
from typing import Any, Dict

from arq.connections import RedisSettings
from arq.cron import cron

from backend.infrastructure.database import AsyncSessionLocal
from backend.domains.submission.service import send_overdue_reminders

logger = logging.getLogger(__name__)


async def check_overdue_submissions(ctx: Dict[str, Any]) -> None:
    """
    [Submission Cron Worker]
    매일 오전 9시에 기한 초과 데이터 요청을 찾아 협력사에게 독촉 알림을 발송한다.
    """
    async with AsyncSessionLocal() as db:
        count = await send_overdue_reminders(db)
    logger.info("[submission_worker] 독촉 알림 발송 완료 (처리 건수: %d)", count)


class WorkerSettings:
    functions = [check_overdue_submissions]
    cron_jobs = [
        cron(check_overdue_submissions, hour=9, minute=0)  # 매일 오전 9시
    ]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    queue_name = "notification_queue"
    max_tries = 3
