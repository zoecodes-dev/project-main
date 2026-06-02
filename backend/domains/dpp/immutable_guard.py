import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.dpp.repository import get_dpp_record


class ImmutableRecordError(Exception):
    """DPP 무결성 위반 시 발생하는 전용 예외입니다."""
    pass


async def assert_not_issued(dpp_id: uuid.UUID, db: AsyncSession) -> None:
    """
    DPP가 'issued' 상태라면 예외를 발생시켜 이후 로직 진행을 차단해요.
    DB 트리거와 함께 데이터 무결성을 보장하는 이중 가드 역할을 합니다.
    """
    dpp = await get_dpp_record(db, dpp_id)
    
    if dpp and dpp.status == "issued":
        raise ImmutableRecordError("이미 발행된 DPP는 수정할 수 없습니다. 정정이 필요하다면 새 버전을 발행해야 합니다.")