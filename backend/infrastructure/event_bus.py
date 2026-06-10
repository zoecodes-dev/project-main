"""
infrastructure/event_bus.py  (담당: 팀원 B)

PostgreSQL LISTEN / NOTIFY 래퍼. 추가 인프라 없이 트랜잭션과 일체화된다.

스펙 1-2 인터페이스:
    async def publish(event_name: str, payload: dict) -> None
    async def subscribe(event_name: str, handler: Callable) -> None

publish는 NOTIFY로 발행, consume은 전용 LISTEN 커넥션으로 수신해
등록된 핸들러로 디스패치한다. (이벤트 양방향: 발행 + 소비)
이벤트 이름·payload는 events/types.py 및 spec 7장 계약을 따른다.

[커넥션 분리 — 풀 고갈 방지]
  LISTEN 루프는 커넥션을 영구 점유한다. 메인 async 세션 풀(engine)을
  그대로 점유하면 일반 API 요청이 쓸 커넥션이 마른다. 따라서 LISTEN 은
  메인 풀과 분리된 전용 raw asyncpg 커넥션에서 돌리고, 앱 종료 시 해제한다.
"""
import asyncio
import json
from typing import Awaitable, Callable, Dict, List, Optional

import asyncpg
from sqlalchemy import text
from backend.core.config import config
from backend.infrastructure.database import engine

# 구독 핸들러 레지스트리. subscribe()로 등록 → LISTEN 루프가 디스패치.
_subscribers: Dict[str, List[Callable[[dict], Awaitable[None]]]] = {}

# LISTEN 전용 백그라운드 태스크 / 커넥션 핸들 (lifespan 이 관리)
_listen_task: Optional[asyncio.Task] = None
_listen_conn: Optional["asyncpg.Connection"] = None


async def publish(event_name: str, payload: dict) -> None:
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
    이벤트 핸들러 등록. 같은 event_name 에 여러 핸들러를 붙일 수 있다.
    실제 호출은 start_event_listener()가 띄운 LISTEN 루프가 담당한다.
    (publish → NOTIFY → LISTEN 수신 → event_name 매칭 → 등록 핸들러 호출)
    """
    _subscribers.setdefault(event_name, []).append(handler)
    print(f"[EVENT SUBSCRIBED] {event_name} <- {handler.__name__}")


async def _dispatch(envelope_raw: str) -> None:
    """
    NOTIFY payload(envelope JSON)를 파싱해 event_name 으로 핸들러를 깨운다.
    한 핸들러가 터져도 다른 핸들러·루프가 죽지 않게 개별 try 로 감싼다.
    """
    try:
        envelope = json.loads(envelope_raw)
    except (json.JSONDecodeError, ValueError):
        print(f"[EVENT WARN] envelope JSON 파싱 실패: {envelope_raw[:120]}")
        return

    event_name = envelope.get("event_name")
    payload = envelope.get("payload", {})
    handlers = _subscribers.get(event_name, [])
    if not handlers:
        return

    for handler in handlers:
        try:
            await handler(payload)
        except Exception as exc:  # 한 핸들러 실패가 전체 디스패치를 깨면 안 됨
            print(f"[EVENT WARN] 핸들러 실패 ({event_name} -> {handler.__name__}): {exc}")


async def _listen_loop() -> None:
    """
    LISTEN 백그라운드 루프 본체.
    메인 async 세션 풀과 분리된 전용 asyncpg 커넥션을 열어 NOTIFY 를 수신한다.
    add_listener 콜백은 동기 컨텍스트라 곧장 await 할 수 없으므로,
    asyncio.create_task 로 _dispatch 를 비동기 스케줄한다.
    """
    global _listen_conn

    # SQLAlchemy DATABASE_URL(예: postgresql+asyncpg://)에서 asyncpg 가 이해할
    # 순수 DSN 으로 정리한다. asyncpg 는 드라이버 접두어를 모른다.
    dsn = config.DATABASE_URL.replace("+asyncpg", "")
    _listen_conn = await asyncpg.connect(dsn=dsn)

    def _on_notify(_conn, _pid, _channel, payload_raw: str) -> None:
        asyncio.create_task(_dispatch(payload_raw))

    await _listen_conn.add_listener(config.KIRA_EVENT_CHANNEL, _on_notify)
    print(f"[EVENT LISTEN] 구독 시작 -> {config.KIRA_EVENT_CHANNEL}")

    try:
        # asyncpg 는 백그라운드에서 NOTIFY 를 수신하므로 루프는 살아만 있으면 된다.
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        # 앱 종료 시 lifespan 이 task.cancel() → 여기로 진입
        raise
    finally:
        if _listen_conn is not None:
            try:
                await _listen_conn.remove_listener(config.KIRA_EVENT_CHANNEL, _on_notify)
                await _listen_conn.close()
            except Exception as exc:
                print(f"[EVENT WARN] LISTEN 커넥션 정리 실패: {exc}")
            _listen_conn = None
        print("[EVENT LISTEN] 구독 종료")


async def start_event_listener() -> None:
    """앱 시작(lifespan)에서 호출. LISTEN 루프를 백그라운드 태스크로 띄운다."""
    global _listen_task
    if _listen_task is None or _listen_task.done():
        _listen_task = asyncio.create_task(_listen_loop())


async def stop_event_listener() -> None:
    """앱 종료(lifespan)에서 호출. LISTEN 태스크를 안전하게 취소·정리한다."""
    global _listen_task
    if _listen_task is not None:
        _listen_task.cancel()
        try:
            await _listen_task
        except asyncio.CancelledError:
            pass
        _listen_task = None