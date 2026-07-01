import os
from typing import Any, Dict

from arq.connections import RedisSettings
from sqlalchemy import text

from backend.infrastructure.database import AsyncSessionLocal
from backend.infrastructure.mail import send_email


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

    channel='email' 이면 적재 후 수신자(users.email)를 해석해 SES 로 실제 발송하고
    status 를 pending→sent/failed 로 전이한다(MAIL_ENABLED=False 면 발송 생략→pending 유지).
    channel='in-app' 은 적재만 하고 끝(프론트가 폴링/조회).
    """
    async with AsyncSessionLocal() as db:
        try:
            inserted = await db.execute(
                text("""
                    INSERT INTO notifications
                        (user_id, channel, notification_type, subject, body, status, dedup_key)
                    VALUES
                        (:user_id, :channel, :ntype, :subject, :body, 'pending', :dedup_key)
                    ON CONFLICT (dedup_key) DO NOTHING
                    RETURNING notification_id
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
            row = inserted.first()
            await db.commit()

            # dedup 로 이미 적재된 건(row None)이면 재발송하지 않는다(멱등).
            if row is None:
                return f"중복 스킵 (dedup_key={dedup_key})"

            if channel == "email":
                to_email = (
                    await db.execute(
                        text("SELECT email FROM users WHERE user_id = :uid"),
                        {"uid": user_id},
                    )
                ).scalar_one_or_none()
                sent = False
                if to_email:
                    sent = await send_email(to_email, subject, body_text=body)
                # MAIL_ENABLED=False/수신자 없음/실패면 pending 유지(추후 재처리 가능),
                # 성공 시에만 sent 로 전이.
                if sent:
                    await db.execute(
                        text("""
                            UPDATE notifications SET status = 'sent', sent_at = now()
                            WHERE dedup_key = :dedup_key
                        """),
                        {"dedup_key": dedup_key},
                    )
                    await db.commit()
                return f"이메일 처리 완료 (dedup_key={dedup_key}, sent={sent})"

            return f"알림 적재 완료 (dedup_key={dedup_key})"
        except Exception as e:
            await db.rollback()
            raise e


class WorkerSettings:
    functions = [process_notification]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    queue_name = "notification_queue"
    max_tries = 3
