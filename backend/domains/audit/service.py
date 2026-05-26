# backend/domains/audit/service.py
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.trace import trace_node
from backend.domains.audit import repository
from backend.domains.audit.models import AuditTrail


class BatchNotFound(Exception):
    """batches 에 batch_id 가 없을 때. router 가 404 로 변환한다."""
    def __init__(self, batch_id: UUID):
        self.batch_id = batch_id
        super().__init__(f"batch not found: {batch_id}")


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


# === 조회·검증 ===

@dataclass
class ChainBreak:
    """해시 체인이 끊긴 지점 한 건 — 강한 신호(위변조 확정)."""
    step_number: int | None
    expected_prev_hash: str | None
    actual_prev_hash: str | None
    reason: str


@dataclass
class ChainWarning:
    """step_number 연속성 이상 — 약한 신호. chain_valid 에는 영향 없음."""
    step_number: int | None
    reason: str


@dataclass
class ChainVerification:
    batch_id: UUID
    total_steps: int
    chain_valid: bool
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

    존재하지 않는 batch_id 면 BatchNotFound 를 던진다 — 없는 배치가
    chain_valid=true 로 통과하는 거짓 양성을 막기 위함(verify 는 적극적 보증 API).

    [chain_valid 강한 신호 — breaks]
      - 첫 step: prev_hash 는 NULL
      - 이후 step: prev_hash == 직전 step 의 output_hash
    [chain_valid 미반영 약한 신호 — warnings]
      - step_number gap / 중복 (정상 흐름일 수 있음)
    """
    if not await repository.batch_exists(db, batch_id):
        raise BatchNotFound(batch_id)

    rows = await repository.list_full_chain(db, batch_id)
    breaks: list[ChainBreak] = []
    warnings: list[ChainWarning] = []

    prev_output: str | None = None
    expected_step: int | None = None
    seen_steps: set[int] = set()

    for idx, row in enumerate(rows):
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