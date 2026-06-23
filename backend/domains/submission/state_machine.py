import uuid
from typing import Optional
import dataclasses
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.exc import IntegrityError
from backend.domains.submission.models import SubmissionStatus, SUBMISSION_TRANSITIONS, DataRequestLog, SubmissionStatusHistory
from backend.events.types import SubmissionStatusChangedEvent

async def transition_submission(
    db: AsyncSession,
    request_id: uuid.UUID,           # 데이터 수집 및 제출 요청건의 유니크 ID (마스터 테이블 외래키)
    to_status: SubmissionStatus,     # 전이하고자 하는 목표 변경 상태 코드 (SubmissionStatus Enum)
    actor_id: uuid.UUID,             # 본 상태 전이 트랜잭션을 발생시킨 실행 주체자 ID (User ID)
    reason: Optional[str] = None,    # 상태 전이 사유 (특히 REVIEW 단계에서 REJECTED 처리 시 반려 근거 기록)
    batch_id: Optional[uuid.UUID] = None   # Provenance 감사 추적 체인 연동을 위한 인프라 전용 식별 파라미터
) -> tuple[DataRequestLog, SubmissionStatusChangedEvent]:
    """
    Pipeline Coordinator: Submission 상태 전이를 제어하는 비즈니스 로직
    """
    
    stmt = select(DataRequestLog).where(DataRequestLog.request_id == request_id).with_for_update()
    result = await db.execute(stmt)
    log_record = result.scalar_one_or_none()
    
    if not log_record:
        raise ValueError("Data request not found")
        
    current_status = log_record.submission_status
    allowed_transitions = SUBMISSION_TRANSITIONS.get(current_status, [])
    
    if to_status not in allowed_transitions:
        raise ValueError(f"Invalid transition from {current_status} to {to_status}")
        
    if to_status == SubmissionStatus.REJECTED and not reason:
        raise ValueError("반려(REJECTED) 상태 전이 시 사유(reason)는 필수입니다.")

    log_record.submission_status = to_status

    # SUBMITTED 전이 시 batch_id 보관 — approve 엔드포인트가 조회해 파이프라인 enqueue에 사용
    if to_status == SubmissionStatus.SUBMITTED and batch_id is not None:
        log_record.batch_id = batch_id

    history = SubmissionStatusHistory(
        request_id=request_id, 
        from_status=current_status.value if hasattr(current_status, 'value') else current_status, 
        to_status=to_status.value if hasattr(to_status, 'value') else to_status, 
        actor_id=actor_id, 
        reason=reason
    )
    
    try:
        db.add(history)
        await db.flush()
    except IntegrityError as e:
        raise ValueError("참조 무결성 위반: 존재하지 않는 사용자(actor_id)입니다.") from e


    event = SubmissionStatusChangedEvent(
        request_id=request_id,
        from_status=current_status.value if current_status else None,
        to_status=to_status.value,
        event_name="SubmissionStatusChanged"
    )
    
    # 트랜잭션 롤백 대비 고스트 이벤트 방지: 서비스 계층에서 DB 커밋 성공 후 발행하도록 이벤트 객체 반환
    return log_record, event
