import uuid
import dataclasses
from datetime import datetime
from typing import Optional
from typing import List

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node, trace_tool
from backend.domains.submission.models import DataRequestLog, SubmissionStatus
from backend.domains.submission.repository import create_data_request, get_data_request, list_data_requests
from backend.domains.submission.state_machine import transition_submission
from backend.events.types import (
    SubmissionRequestedEvent,
    SubmissionStartedEvent,
    SubmissionCompletedEvent,
    SubmissionApprovedEvent,
    SubmissionRejectedEvent,
)

@trace_node("create_and_request_submission", node_type="agent")
async def create_and_request_submission(
    db: AsyncSession,
    requester_user_id: uuid.UUID,
    target_supplier_id: uuid.UUID,
    requested_data_type: str,
    due_date: datetime,
    actor_id: uuid.UUID
) -> DataRequestLog:
    """
    [Pipeline Coordinator]
    새로운 데이터 제출 요청을 생성하고 초기 상태 전이를 오케스트레이션합니다.
    
    [로직 흐름]
    1. DB 삽입: PENDING 상태로 마스터(DataRequestLog) 레코드를 안전하게 초기 생성합니다.
       (IntegrityError 발생 시 422 에러로 변환해 프레임워크 예외 처리를 돕습니다.)
    2. 상태 전이: transition_submission()을 호출해 REQUESTED 상태로 전이합니다.
       (상태 머신을 통하므로 감사 테이블에 이력이 남고 무결성이 보장됩니다.)
    3. 이벤트 발행: 모든 트랜잭션이 성공한 후 'SubmissionRequested' 이벤트를 발행합니다.
       (규칙 준수: db 인자 없이 2-인자로 publish를 호출하여 외부 도메인/Notification과 결합도를 낮춥니다.)
    """
    new_log = DataRequestLog(
        requester_user_id=requester_user_id,
        target_supplier_id=target_supplier_id,
        requested_data_type=requested_data_type,
        due_date=due_date,
        submission_status=SubmissionStatus.PENDING
    )
    
    try:
        req_log = await create_data_request(db, new_log)
        # 2. 전이 규칙을 통한 상태 변경 (PENDING -> REQUESTED)
        _, status_event = await transition_submission(
            db=db,
            request_id=req_log.request_id,
            to_status=SubmissionStatus.REQUESTED,
            actor_id=actor_id,
            reason="최초 공급망 데이터 제출 요청 생성"
        )
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise ValueError("참조 무결성 위반: 존재하지 않는 협력사 또는 사용자 ID입니다.") from e
    except Exception:
        await db.rollback()
        raise

    event = SubmissionRequestedEvent(
        request_id=req_log.request_id,
        supplier_id=target_supplier_id,
        due_date=due_date,
        event_name="SubmissionRequested"
    )
    await publish("SubmissionRequested", dataclasses.asdict(event))
    await publish("SubmissionStatusChanged", dataclasses.asdict(status_event))
    
    return req_log

@trace_tool("get_submission_detail")
async def get_submission_detail(db: AsyncSession, request_id: uuid.UUID) -> Optional[DataRequestLog]:
    """
    [조회 도구] 단건 요청 내역 상세 조회
    - 상태를 변경하지 않는 순수 조회이므로 @trace_node 대신 @trace_tool을 적용합니다.
    - 에이전트나 프론트엔드가 특정 Submission의 현재 상태를 확인할 때 호출됩니다.
    """
    return await get_data_request(db, request_id)

@trace_tool("get_submissions_list")
async def get_submissions_list(
    db: AsyncSession,
    supplier_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
) -> list[DataRequestLog]:
    """
    [조회 도구] 목록 필터링 조회
    - 협력사(supplier_id)나 현재 진행 상태(status)를 기준으로 다건을 조회합니다.
    """
    return await list_data_requests(db, supplier_id, status, skip, limit)

@trace_node("update_submission_status", node_type="agent")
async def update_submission_status(
    db: AsyncSession,
    request_id: uuid.UUID,
    to_status: SubmissionStatus,
    actor_id: uuid.UUID,
    reason: Optional[str] = None,
    batch_id: Optional[str] = None,
    file_urls: Optional[list[str]] = None
) -> Optional[DataRequestLog]:
    """
    [Pipeline Coordinator] 단건 데이터 요청 상태 강제 전이
    - 외부 API(PATCH) 호출이나 내부 에이전트 판단에 의해 상태를 변경할 때 사용됩니다.
    - 반드시 state_machine.py의 transition_submission()을 거쳐 허용된 전이인지 검증합니다.
    - 전이 완료 후 목표 상태(to_status)에 맞는 후속 도메인 이벤트를 분기 발행합니다.
    """
    # [예외 방어] 제출 완료 시 Provenance(감사 추적)를 위한 batch_id 필수 강제
    if to_status == SubmissionStatus.SUBMITTED:
        if not batch_id:
            raise ValueError("제출 완료(SUBMITTED) 상태로 전이하려면 반드시 AI 파이프라인용 batch_id가 필요합니다.")
        if not file_urls:
            raise ValueError("제출 완료(SUBMITTED) 상태로 전이하려면 최소 1개 이상의 파일(file_urls)이 첨부되어야 합니다.")

    try:
        req_log, status_event = await transition_submission(
            db=db,
            request_id=request_id,
            to_status=to_status,
            actor_id=actor_id,
            reason=reason,
            batch_id=batch_id
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    # 공통: 상태 전이 완료(커밋 성공) 후 상태 변경 이벤트 일괄 발행
    await publish("SubmissionStatusChanged", dataclasses.asdict(status_event))

    # 도메인 이벤트 발행: IN_PROGRESS(제출 시작) / SUBMITTED(제출 완료) / APPROVED(승인) / REJECTED(반려)
    if to_status == SubmissionStatus.IN_PROGRESS and req_log:
        await publish("SubmissionStarted", dataclasses.asdict(SubmissionStartedEvent(
            request_id=request_id, supplier_id=req_log.target_supplier_id, event_name="SubmissionStarted"
        )))
    elif to_status == SubmissionStatus.SUBMITTED and req_log:
        await publish("SubmissionCompleted", dataclasses.asdict(SubmissionCompletedEvent(
            request_id=request_id, batch_id=batch_id, file_urls=file_urls or [], event_name="SubmissionCompleted"
        )))
    elif to_status == SubmissionStatus.APPROVED and req_log:
        await publish("SubmissionApproved", dataclasses.asdict(SubmissionApprovedEvent(
            request_id=request_id, batch_id=batch_id, event_name="SubmissionApproved"
        )))
    elif to_status == SubmissionStatus.REJECTED and req_log:
        await publish("SubmissionRejected", dataclasses.asdict(SubmissionRejectedEvent(
            request_id=request_id, reason=reason, event_name="SubmissionRejected"
        )))

    return req_log
