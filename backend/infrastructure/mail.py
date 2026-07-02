"""
infrastructure/mail.py — AWS SES 이메일 전송 래퍼 (횡단 관심사)

boto3 SES는 동기 API라 asyncio.to_thread 로 감싸 이벤트 루프를 막지 않는다.
자격증명은 boto3 기본 체인(EC2 IAM Role / 로컬 ~/.aws)에서 자동 해석한다(INFRA-5).

[안전 스위치] config.MAIL_ENABLED=False 또는 MAIL_FROM 미설정이면 실제 발송 없이
  no-op(로그만) 하고 False 를 반환한다. → SES 발신 identity 검증 전/로컬/CI에서도
  코드가 안 깨진다. 운영에서 .env 로 MAIL_ENABLED=true + MAIL_FROM=<검증주소> 설정 시 발송.

도메인 계층이 아니라 infra 계층이라, 어느 도메인/핸들러/워커에서든 import 해서 쓴다.
"""
import asyncio
import logging
from typing import Iterable, Optional, Union

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from backend.core.config import config

logger = logging.getLogger(__name__)

_ses_client = None


def _client():
    """SES 클라이언트 지연 생성(첫 발송 시). region 은 config.AWS_REGION."""
    global _ses_client
    if _ses_client is None:
        _ses_client = boto3.client("ses", region_name=config.AWS_REGION)
    return _ses_client


def _send_sync(to_addresses, subject, body_text, body_html):
    body: dict = {}
    if body_text:
        body["Text"] = {"Data": body_text, "Charset": "UTF-8"}
    if body_html:
        body["Html"] = {"Data": body_html, "Charset": "UTF-8"}
    return _client().send_email(
        Source=config.MAIL_FROM,
        Destination={"ToAddresses": to_addresses},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": body,
        },
    )


async def send_email(
    to: Union[str, Iterable[str]],
    subject: str,
    body_text: Optional[str] = None,
    body_html: Optional[str] = None,
) -> bool:
    """이메일 1건 발송. 성공 시 True, 비활성/실패 시 False (예외를 밖으로 던지지 않음).

    호출부는 반환값으로 발송 여부만 판단하고, 실패해도 흐름이 죽지 않도록 설계한다
    (초대/알림은 best-effort). 하드 실패가 필요하면 반환 False 를 보고 재시도 큐에 태운다.
    """
    to_addresses = [to] if isinstance(to, str) else [a for a in to if a]
    if not to_addresses:
        return False

    if not config.MAIL_ENABLED or not config.MAIL_FROM:
        logger.info(
            "[mail] 비활성(MAIL_ENABLED=%s, from=%r) — 발송 생략: to=%s subject=%r",
            config.MAIL_ENABLED, config.MAIL_FROM, to_addresses, subject,
        )
        return False

    if not (body_text or body_html):
        body_text = subject  # 본문 없으면 제목을 본문으로

    try:
        resp = await asyncio.to_thread(_send_sync, to_addresses, subject, body_text, body_html)
        logger.info("[mail] SES 발송 완료 to=%s msg_id=%s", to_addresses, resp.get("MessageId"))
        return True
    except (BotoCoreError, ClientError) as exc:
        logger.error("[mail] SES 발송 실패 to=%s: %s", to_addresses, exc)
        return False
