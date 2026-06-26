import os
from typing import Any, Dict

from arq.connections import RedisSettings
from sqlalchemy import text

from backend.infrastructure.database import AsyncSessionLocal


async def process_notification(
    ctx: Dict[str, Any],
    user_id: str,
    channel: str,
    notification_type: str,
    subject: str,
    body: str,
    dedup_key: str,
) -> str:
    """
    [Notification Queue Worker]
    알림 1건을 notifications 테이블에 적재한다.
    dedup_key UNIQUE + ON CONFLICT DO NOTHING 으로 중복 발송 방어(멱등).
    """
    async with AsyncSessionLocal() as db:
        try:
            await db.execute(
                text("""
                    INSERT INTO notifications
                        (user_id, channel, notification_type, subject, body, status, dedup_key)
                    VALUES
                        (:user_id, :channel, :ntype, :subject, :body, 'pending', :dedup_key)
                    ON CONFLICT (dedup_key) DO NOTHING
                """),
                {
                    "user_id": user_id,
                    "channel": channel,
                    "ntype": notification_type,
                    "subject": subject,
                    "body": body,
                    "dedup_key": dedup_key,
                },
            )
            await db.commit()
            return f"알림 적재 완료 (dedup_key={dedup_key})"
        except Exception as e:
            await db.rollback()
            raise e


class WorkerSettings:
    functions = [process_notification]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    queue_name = "notification_queue"
    max_tries = 3
