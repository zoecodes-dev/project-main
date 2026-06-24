import uuid
import dataclasses

from backend.infrastructure.queue import enqueue, VERIFICATION_QUEUE
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_tool
from sqlalchemy.ext.asyncio import AsyncSession
from backend.events.types import (
    VerificationStartedEvent,
    VerificationFailedEvent,
    VerificationCompletedEvent
)
from sqlalchemy import text

# 수치 허용오차: ±5% (확정값 기준 5% 이내 오차는 통과)
_NUMERIC_TOLERANCE = 0.05

@trace_tool("verify_document_integrity_rule")
async def verify_document_integrity_rule(
    db: AsyncSession,
    batch_id: uuid.UUID,
    supplier_id: uuid.UUID,
    confirmed_fields: dict,
) -> bool:
    """
    [Verification Engine] 문서 무결성 검증
    협력사 확정값(confirmed_fields)과 업로드 증빙 문서 추출값을 대조해
    불일치 시 compliance_reject 판정 + HITL 플래그.
    비교 대상 없음(빈 페어) → 통과.
    수치 허용오차 ±5%, 문자열은 strip 후 동등 비교.
    """
    from backend.agents.data_gateway import get_integrity_pairs

    pairs = await get_integrity_pairs(db, supplier_id, confirmed_fields)

    mismatches = []
    for p in pairs:
        if p["value_type"] == "numeric":
            c_val, d_val = float(p["confirmed_value"]), float(p["document_value"])
            base = abs(c_val) if c_val != 0 else abs(d_val)
            if base > 0 and abs(c_val - d_val) / base > _NUMERIC_TOLERANCE:
                mismatches.append(p)
        else:
            if str(p["confirmed_value"]).strip() != str(p["document_value"]).strip():
                mismatches.append(p)

    if mismatches:
        field_list = ", ".join(p["field"] for p in mismatches)
        await db.execute(
            text("""
                INSERT INTO compliance_results
                    (batch_id, regulation_id, supplier_id, verdict, needs_human_review, reasoning_text)
                VALUES
                    (:batch_id, NULL, :supplier_id, 'compliance_reject', TRUE, :reasoning_text)
            """),
            {
                "batch_id": batch_id,
                "supplier_id": supplier_id,
                "reasoning_text": f"문서 무결성 불일치 {len(mismatches)}건: {field_list}",
            },
        )
        await db.flush()
        return False

    return True


@trace_tool("get_compliance_history_dto")
async def get_compliance_history_dto(db: AsyncSession, batch_id: uuid.UUID) -> list[dict]:
    """[조회 전용 DTO 헬퍼] HITL 등 타 도메인에서 컴플라이언스 이력을 조회할 때 사용합니다."""
    stmt = text("""
        SELECT verdict, reasoning_text, supplier_id, regulation_id
        FROM compliance_results
        WHERE batch_id = :batch_id
    """)
    result = await db.execute(stmt, {"batch_id": str(batch_id)})
    return [dict(r._mapping) for r in result.fetchall()]

@trace_tool("verify_feoc_rule")
async def verify_feoc_rule(db: AsyncSession, batch_id: uuid.UUID, supplier_id: uuid.UUID, direct_ownership: float, indirect_ownership: float = 0.0) -> bool:
    """
    [Verification Engine] FEOC 지분율 규제 심사
    - Decision #4 반영: 직접 지분 25% 이상은 즉시 위반(compliance_violation) 처리
    - 간접/합산 지분 25% 이상은 위반 + needs_human_review (HITL 사람 확인 필요)
    """
    await publish("VerificationStarted", dataclasses.asdict(VerificationStartedEvent(batch_id=batch_id, rules_applied=["FEOC"], event_name="VerificationStarted")))

    total_ownership = direct_ownership + indirect_ownership
    is_violation = False
    reason = ""
    needs_human_review = False # gray_zone 플래그 역할

    if direct_ownership >= 25.0:
        is_violation = True
        reason = f"FEOC 직접 지분율 25% 이상 위반 (현재: {direct_ownership}%)"
    elif indirect_ownership >= 25.0:
        is_violation = True
        reason = f"FEOC 간접 지분율 25% 이상 위반 (간접: {indirect_ownership}%)"
        needs_human_review = True # Decision #4: 간접 지분 위반 시에만 HITL 큐로 보내기 위한 표식
    elif total_ownership >= 25.0:
        is_violation = True
        reason = f"FEOC 합산 지분율 25% 이상 위반 (총합: {total_ownership}%)"
        # 합산 지분 위반은 규정상 즉시 위반 처리 (needs_human_review = False 유지)
        
    if is_violation:
        # 비동기 큐 작업 위임 스펙 준수 (인자 이름 일치)
        await enqueue(
            VERIFICATION_QUEUE, 
            "process_feoc_violation", 
            batch_id=batch_id, 
            supplier_id=supplier_id, 
            direct_ownership=direct_ownership, 
            indirect_ownership=indirect_ownership, 
            reason=reason,
            needs_human_review=needs_human_review,
            job_id=f"feoc_violation_{batch_id}_{supplier_id}"
        )
        await publish("VerificationFailed", dataclasses.asdict(VerificationFailedEvent(batch_id=batch_id, violated_rules=["FEOC"], event_name="VerificationFailed")))
        return False

    await publish("VerificationCompleted", dataclasses.asdict(VerificationCompletedEvent(batch_id=batch_id, results=[{"rule": "FEOC", "passed": True}], event_name="VerificationCompleted")))
    return True
