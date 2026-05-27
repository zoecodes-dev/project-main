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

[입력 해시 대상 선정 — 함수/메서드 혼용 대응]
    이 데코레이터는 메서드(self를 첫 인자로 받음)와 일반 함수(첫 인자가
    진짜 데이터)에 모두 붙는다. 과거엔 args[1:]로 무조건 첫 인자를 버려
    일반 함수의 첫 인자(request_id 등)가 해시에서 누락됐다.
    이제는 타입으로 판별한다:
      - AsyncSession 인스턴스 → DB 세션이므로 해시 대상에서 제외
      - dict/기본타입(str,int,float,bool,UUID,None)이 아닌 객체 → self로 추정, 제외
      - 그 외(진짜 데이터 인자) → 해시 대상에 포함
"""
import functools
import hashlib
import json
import time
from typing import Any, Callable, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession 

# 해시 대상에 그대로 포함하는 "값 타입"
_VALUE_TYPES = (str, int, float, bool, UUID)


def _stable_hash(obj: Any) -> str:
    """
    [데이터 지장 찍기]
    입력된 객체를 글자(JSON)로 바꾼 뒤 SHA-256 알고리즘으로 64자리 고유 해시값을 만듭니다.
    내용이 0.0001%만 바뀌어도 완전히 다른 해시값이 나와 위변조를 방지합니다.
    """
    try:
        serialized = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = str(obj)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _hashable_args(args: tuple) -> list:
    """
    [입력 해시 대상 골라내기]
    위치 인자 중 진짜 데이터만 남긴다.
    - DB 세션(AsyncSession)은 제외 (기록 대상이 아니라 도구)
    - dict는 포함 (state dict 등 진짜 입력)
    - 값 타입(str/int/float/bool/UUID)은 포함
    - 그 외 클래스 인스턴스(메서드의 self로 추정)는 제외
    이렇게 하면 일반 함수의 첫 인자(request_id 등)는 보존되고,
    메서드의 self나 DB 세션만 빠진다.
    """
    kept = []
    for a in args:
        if isinstance(a, AsyncSession):
            continue                      # DB 세션 제외
        if isinstance(a, dict) or isinstance(a, _VALUE_TYPES) or a is None:
            kept.append(a)                # 진짜 데이터 포함
            continue
        # dict도 값 타입도 아닌 객체 = self(클래스 인스턴스)로 추정 → 제외
        continue
    return kept


def _extract_batch_id(args: tuple, kwargs: dict) -> Optional[str]:
    """
    [배치 ID 찾기]
    함수에 들어온 인자(args, kwargs) 뒤져서 어떤 작업(batch_id)에 대한 기록인지 찾아냅니다.
    """
    if "batch_id" in kwargs and kwargs["batch_id"] is not None:
        return str(kwargs["batch_id"])
    for arg in args:
        if isinstance(arg, dict) and arg.get("batch_id"):
            return str(arg["batch_id"])
    return None


def _extract_session(args: tuple, kwargs: dict) -> Optional[AsyncSession]:
    """
    [DB 연결통로 찾기]
    DB에 기록을 남겨야 하므로, 함수 인자 중에서 SQLAlchemy의 DB 세션(AsyncSession)을 찾아냅니다.
    """
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
    """
    [해시 체인 연결 및 DB 저장]
    1. 이 배치의 바로 직전 기록을 조회해서 그 기록의 '결과 지장(output_hash)'을 가져옵니다.
    2. 그 값을 나의 '이전 지장(prev_hash)' 칸에 넣어서 체인처럼 엮어버립니다. (순서 조작 방지)
    3. 단계 번호(step_number)를 1 올린 뒤 audit_trail 테이블에 최종 INSERT 합니다.
    """
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
    [핵심 스티커 - 에이전트 노드용]
    팀원들이 상태 변경 함수 위에 @trace_node("노드명")을 붙이면 작동하는 메인 데코레이터입니다.
    함수 실행 전후의 시간을 재고, 인자와 결과값을 자동으로 해싱하여 DB에 기록합니다.

    input_hash는 _hashable_args로 self/DB세션을 제외한 '진짜 데이터 인자'와
    kwargs를 함께 해싱한다(메서드/일반함수 모두 첫 인자가 누락되지 않음).
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            input_hash = _stable_hash(
                {"args": _hashable_args(args), "kwargs": kwargs}
            )

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
    """
    [핵심 스티커 - 외부 툴/API용]
    외부 API 호출이나 단순 DB 쿼리를 수행하는 함수 위에 @trace_tool("툴명")으로 붙여 사용합니다.
    """
    return trace_node(node_name=tool_name, node_type="tool")