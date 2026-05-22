"""
infrastructure/event_bus.py  (담당: 팀원 B)

PostgreSQL LISTEN / NOTIFY 래퍼. 추가 인프라 없이 트랜잭션과 일체화된다.

스펙 1-2 인터페이스:
    async def publish(event_name: str, payload: dict) -> None
    async def subscribe(event_name: str, handler: Callable) -> None

W1 범위: publish만 동작. subscribe는 핸들러 등록만(실제 LISTEN 루프는 W2).
이벤트 이름·payload는 events/types.py 및 spec 7장 계약을 따른다.
"""
import json
from typing import Awaitable, Callable, Dict, List
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.config import config
from backend.infrastructure.database import engine

# W1: 구독 핸들러 등록만. 실제 디스패치는 W2 LISTEN 루프에서.
_subscribers: Dict[str, List[Callable[[dict], Awaitable[None]]]] = {}


async def publish(db: AsyncSession, event_name: str, payload: dict) -> None:
    """
    이벤트를 PostgreSQL NOTIFY로 발행한다.
    payload는 JSON 직렬화 가능한 dict여야 한다.
    envelope: {"event_name": ..., "payload": {...}}
    """
    envelope = json.dumps(
        {"event_name": event_name, "payload": payload},
        default=str,
        ensure_ascii=False,
    )
    # NOTIFY는 페이로드 8000바이트 제한 → 큰 payload는 큐/테이블 참조로 우회 (W2)
    async with engine.connect() as conn:
        await conn.execute(
            text("SELECT pg_notify(:channel, :msg)"),
            {"channel": config.KIRA_EVENT_CHANNEL, "msg": envelope},
        )
        await conn.commit()
    print(f"[EVENT PUBLISHED] {event_name} -> NOTIFY {config.KIRA_EVENT_CHANNEL}")


async def subscribe(
    event_name: str,
    handler: Callable[[dict], Awaitable[None]],
) -> None:
    """
    이벤트 핸들러 등록. W1에서는 등록만 수행한다.
    실제 LISTEN 구독 루프는 W2에서 구현한다.
    """
    _subscribers.setdefault(event_name, []).append(handler)
    print(f"[EVENT SUBSCRIBED] {event_name} <- {handler.__name__}")