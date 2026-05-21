"""
infrastructure/trace.py  (담당: 팀원 B) — 가장 중요

모든 상태 변경이 audit_trail에 자동 기록되도록 하는 데코레이터.

스펙 1-4 인터페이스:
    @trace_node(node_name, node_type="agent")
    @trace_tool(tool_name)

audit_trail 컬럼 매핑:
    node_type   : 데코레이터 인자 (agent / tool / human)
    node_name   : 데코레이터 인자
    input_hash  : SHA-256(함수 인자 JSON 직렬화)
    output_hash : SHA-256(반환값 JSON 직렬화)
    prev_hash   : 같은 batch_id의 직전 output_hash (해시 체인 핵심)
    duration_ms : 실행 시간

규칙: batch_id가 없으면 None 허용(테스트 편의). 추적 실패가
      비즈니스 로직을 깨뜨리면 안 되므로 기록 실패는 로그만 남기고 통과.
"""
import functools
import hashlib
import json
import time
from typing import Any, Callable, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _stable_hash(obj: Any) -> str:
    """JSON 직렬화 가능한 객체를 SHA-256으로 해싱. 직렬화 불가 값은 str 폴백."""
    try:
        serialized = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = str(obj)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _extract_batch_id(args: tuple, kwargs: dict) -> Optional[str]:
    """함수 인자에서 batch_id를 추출. kwargs 우선, 없으면 BatchState dict 탐색."""
    if "batch_id" in kwargs and kwargs["batch_id"] is not None:
        return str(kwargs["batch_id"])
    for arg in args:
        if isinstance(arg, dict) and arg.get("batch_id"):
            return str(arg["batch_id"])
    return None


def _extract_session(args: tuple, kwargs: dict) -> Optional[AsyncSession]:
    """함수 인자에서 AsyncSession을 추출 (audit_trail INSERT용)."""
    if "db" in kwargs and isinstance(kwargs["db"], AsyncSession):
        return kwargs["db"]
    for arg in args:
        if isinstance(arg, AsyncSession):
            return arg
    return None


async def _write_audit(
    db: AsyncSession,
    batch_id: Optional[str],
    node_type: str,
    node_name: str,
    input_hash: str,
    output_hash: str,
    duration_ms: int,
) -> None:
    """audit_trail에 INSERT. 같은 batch_id의 직전 output_hash를 prev_hash로 연결."""
    if batch_id is None:
        # batch_id 없으면 해시 체인 구성 불가 → 기록 생략 (테스트 편의)
        return

    prev_row = await db.execute(
        text(
            """
            SELECT output_hash, step_number
            FROM audit_trail
            WHERE batch_id = :batch_id
            ORDER BY step_number DESC
            LIMIT 1
            """
        ),
        {"batch_id": batch_id},
    )
    prev = prev_row.first()
    prev_hash = prev[0] if prev else None
    step_number = (prev[1] + 1) if prev else 1

    await db.execute(
        text(
            """
            INSERT INTO audit_trail
                (batch_id, step_number, node_type, node_name,
                 input_hash, output_hash, prev_hash, duration_ms)
            VALUES
                (:batch_id, :step_number, :node_type, :node_name,
                 :input_hash, :output_hash, :prev_hash, :duration_ms)
            """
        ),
        {
            "batch_id": batch_id,
            "step_number": step_number,
            "node_type": node_type,
            "node_name": node_name,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "prev_hash": prev_hash,
            "duration_ms": duration_ms,
        },
    )
    await db.commit()


def trace_node(node_name: str, node_type: str = "agent"):
    """
    함수 실행 전후의 input/output을 SHA-256 해싱하고 audit_trail에 INSERT.
    상태 변경 함수(에이전트 노드)에 필수 적용.
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            input_hash = _stable_hash({"args": args[1:], "kwargs": kwargs})

            result = await func(*args, **kwargs)

            duration_ms = int((time.perf_counter() - start) * 1000)
            output_hash = _stable_hash(result)

            db = _extract_session(args, kwargs)
            batch_id = _extract_batch_id(args, kwargs)
            if db is not None:
                try:
                    await _write_audit(
                        db, batch_id, node_type, node_name,
                        input_hash, output_hash, duration_ms,
                    )
                except Exception as exc:  # 추적 실패가 비즈니스 로직을 깨면 안 됨
                    print(f"[TRACE WARN] audit_trail 기록 실패 ({node_name}): {exc}")

            return result

        return wrapper

    return decorator


def trace_tool(tool_name: str):
    """외부 API 호출, DB 쿼리 등 툴 단위 추적 (단순 버전)."""
    return trace_node(node_name=tool_name, node_type="tool")
