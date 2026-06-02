import uuid
import dataclasses
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.dpp.models import DppRecord
from backend.domains.dpp.repository import get_dpp_record
from backend.domains.dpp.immutable_guard import assert_not_issued
from backend.infrastructure.trace import trace_node
from backend.infrastructure.event_bus import publish
from backend.domains.dpp.service import DPPIssuedEvent


@trace_node("issue_dpp", "agent")
async def issue_dpp(db: AsyncSession, dpp_id: uuid.UUID) -> DppRecord:
    """
    DPP를 최종 발행('issued') 상태로 전이시켜요.
    """
    # 1. 애플리케이션 레벨 이중 가드 통과 확인 (이미 발행되었다면 여기서 예외 발생)
    await assert_not_issued(dpp_id, db)
    
    dpp = await get_dpp_record(db, dpp_id)
    if not dpp:
        raise ValueError("해당 DPP를 찾을 수 없습니다.")

    # 2. 상태 전이 및 발행 시각 기록
    dpp.status = "issued"
    dpp.issued_at = datetime.now(timezone.utc)

    # 3. DB 반영 및 이벤트 발행
    await db.commit()
    await db.refresh(dpp)
    
    event = DPPIssuedEvent(
        dpp_id=dpp.dpp_id,
        product_id=dpp.product_id,
        qr_code_url=dpp.qr_code_url or ""
    )
    await publish("DPPIssued", dataclasses.asdict(event))

    return dpp


@trace_node("revoke_dpp", "agent")
async def revoke_dpp(db: AsyncSession, dpp_id: uuid.UUID) -> DppRecord:
    """
    DPP를 폐기('revoked') 상태로 전이시켜요.
    """
    dpp = await get_dpp_record(db, dpp_id)
    if not dpp:
        raise ValueError("해당 DPP를 찾을 수 없습니다.")
        
    dpp.status = "revoked"

    await db.commit()
    await db.refresh(dpp)
    return dpp