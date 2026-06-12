import dataclasses
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.risk.models import RiskProfile
from backend.domains.risk.repository import RiskRepository
from backend.domains.risk.state_machine import update_risk_profile_state
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node
from backend.events.types import RiskEscalatedEvent


@trace_node(node_name="calculate_risk_score", node_type="agent")
async def calculate_risk_score(db: AsyncSession, batch_id: UUID, supplier_id: UUID, violations: list[dict]) -> dict:
    """
    여러 에이전트(Verification, Geo, Compliance)의 검증 결과를 받아 가점식으로 위험 점수를 합산합니다.
    - compliance_violation / compliance_reject: +50
    - GeoRiskDetected: +30
    - compliance_warning: +15
    """
    profile = await RiskRepository.get_by_supplier_id(db, supplier_id)
    if not profile:
        try:
            profile = await RiskRepository.create(db, RiskProfile(supplier_id=supplier_id))
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=404, detail=f"협력사(supplier_id={supplier_id})가 존재하지 않습니다.")
        
    current_score = profile.overall_risk_score or 0
    reasons = list(profile.high_risk_reasons) if profile.high_risk_reasons else []
    
    added_score = 0
    for violation in violations:
        v_type = violation.get("type")
        if v_type in ("compliance_violation", "compliance_reject"):
            added_score += 50
        elif v_type == "GeoRiskDetected":
            added_score += 30
        elif v_type == "compliance_warning":
            added_score += 15
        
        if violation.get("reason"):
            reasons.append(violation.get("reason"))
        
    new_score = current_score + added_score
    
    is_escalated = update_risk_profile_state(profile, new_score, reasons)
    
    # 응답 및 이벤트용 변수를 커밋 전에 안전하게 뽑아둡니다 (Session Expire 방어)
    final_score = profile.overall_risk_score
    final_level = profile.risk_level
    
    # 이벤트 발행 전 반드시 DB에 상태를 영속화(Commit) 합니다.
    await db.commit()
    
    if is_escalated:
        event = RiskEscalatedEvent(
            batch_id=batch_id,
            reason=f"Risk score reached {final_score} (critical)"
        )
        await publish("RiskEscalated", dataclasses.asdict(event))
        
    return {
        "supplier_id": str(supplier_id),
        "overall_risk_score": final_score,
        "risk_level": final_level,
        "is_escalated": is_escalated
    }