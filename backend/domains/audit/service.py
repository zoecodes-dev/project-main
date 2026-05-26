# backend/domains/audit/service.py
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.trace import trace_node
from backend.domains.audit import repository
from backend.domains.audit.models import AuditTrail


@trace_node(node_name="create_audit_entry", node_type="human")
async def create_audit_entry(
    db: AsyncSession,
    batch_id: UUID,
    decision_text: str,
) -> dict:
    """
    human 노드 결정 기록용 깡통 함수.
    실제 INSERT는 @trace_node 데코레이터가 처리.
    decision_text / citations 직접 저장은 W2.
    """
    # TODO W2: decision_text / citations 컬럼 직접 UPDATE 로직 추가
    return {
        "batch_id": str(batch_id),
        "decision_text": decision_text,
        "status": "recorded",
    }


# === 조회·검증 (이번 주 할일 1·2) ===

@dataclass
class ChainBreak:
    """해시 체인이 끊긴 지점 한 건 — 강한 신호(위변조 확정)."""
    step_number: int | None
    expected_prev_hash: str | None   # 직전 row의 output_hash
    actual_prev_hash: str | None     # 이 row에 실제 박힌 prev_hash
    reason: str


@dataclass
class ChainWarning:
    """step_number 연속성 이상 — 약한 신호(사람이 맥락 판단). chain_valid 에는 영향 없음."""
    step_number: int | None
    reason: str


@dataclass
class ChainVerification:
    batch_id: UUID
    total_steps: int
    chain_valid: bool                       # 해시 무결성만 반영
    breaks: list[ChainBreak] = field(default_factory=list)
    warnings: list[ChainWarning] = field(default_factory=list)


async def get_trail(
    db: AsyncSession,
    batch_id: UUID,
    node_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[AuditTrail]:
    """배치의 audit_trail을 step_number 순으로 조회 (node_type/기간 필터 선택)."""
    return await repository.list_trail_by_batch(db, batch_id, node_type, start, end)


async def verify_chain(db: AsyncSession, batch_id: UUID) -> ChainVerification:
    """
    해시 체인 무결성 검증.

    [chain_valid 에 반영되는 강한 신호 — breaks]
      - 첫 step: prev_hash 는 NULL
      - 이후 step: prev_hash == 직전 step 의 output_hash
      하나라도 어긋나면 chain_valid=False.

    [chain_valid 에 반영되지 않는 약한 신호 — warnings]
      - step_number 가 1씩 연속이 아님 (gap) — 꼬리 잘림 등 삭제 탐지 보조
      - step_number 중복
      gap·중복은 정상 흐름(HITL rejected·재시도)일 수 있으므로 경고로만 둔다.
    """
    rows = await repository.list_full_chain(db, batch_id)
    breaks: list[ChainBreak] = []
    warnings: list[ChainWarning] = []

    prev_output: str | None = None    # 직전 row의 output_hash. 첫 row 직전은 None.
    expected_step: int | None = None  # 직전 step_number + 1. 연속성 비교용.
    seen_steps: set[int] = set()

    for idx, row in enumerate(rows):
        # --- 해시 체인 (강한 신호) ---
        if idx == 0:
            if row.prev_hash is not None:
                breaks.append(ChainBreak(
                    step_number=row.step_number,
                    expected_prev_hash=None,
                    actual_prev_hash=row.prev_hash,
                    reason="first_step_prev_hash_not_null",
                ))
        else:
            if row.prev_hash != prev_output:
                breaks.append(ChainBreak(
                    step_number=row.step_number,
                    expected_prev_hash=prev_output,
                    actual_prev_hash=row.prev_hash,
                    reason="prev_hash_mismatch",
                ))
        prev_output = row.output_hash

        # --- step_number 연속성 (약한 신호) ---
        if row.step_number is not None:
            if row.step_number in seen_steps:
                warnings.append(ChainWarning(
                    step_number=row.step_number,
                    reason="duplicate_step_number",
                ))
            seen_steps.add(row.step_number)

            if expected_step is not None and row.step_number != expected_step:
                warnings.append(ChainWarning(
                    step_number=row.step_number,
                    reason="step_number_gap",
                ))
            expected_step = row.step_number + 1

    return ChainVerification(
        batch_id=batch_id,
        total_steps=len(rows),
        chain_valid=len(breaks) == 0,
        breaks=breaks,
        warnings=warnings,
    )