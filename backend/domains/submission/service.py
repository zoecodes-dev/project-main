import uuid
import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Optional
from typing import List

from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node, trace_tool
from backend.domains.submission.models import DataRequestLog, SubmissionStatus
from backend.domains.submission.repository import (
    create_data_request, 
    get_data_request, 
    list_data_requests,
    get_completeness_by_supplier,
    get_timeline_by_supplier
)
from backend.domains.submission.state_machine import transition_submission
from backend.domains.submission.models import DataCompletenessStatus, SubmissionStatusHistory
from backend.events.types import (
    SubmissionRequestedEvent,
    SubmissionStartedEvent,
    SubmissionCompletedEvent,
    SubmissionApprovedEvent,
    SubmissionRejectedEvent,
    SubmissionStatusChangedEvent
)

@trace_node("create_and_request_submission", node_type="agent")
async def create_and_request_submission(
    db: AsyncSession,
    requester_user_id: uuid.UUID,
    target_supplier_id: uuid.UUID,
    requested_data_type: str,
    due_date: Optional[datetime],
    actor_id: uuid.UUID
) -> DataRequestLog:
    """
    [Pipeline Coordinator]
    새로운 데이터 제출 요청을 생성하고 초기 상태 전이를 오케스트레이션합니다.
    
    [로직 흐름]
    1. DB 삽입: 허용값인 REQUESTED 상태로 마스터(DataRequestLog) 레코드를 안전하게 초기 생성합니다.
       (IntegrityError 발생 시 422 에러로 변환해 프레임워크 예외 처리를 돕습니다.)
    2. 히스토리 기록: 생성 즉시 첫 번째 History를 수동으로 남깁니다.
    3. 이벤트 발행: 모든 트랜잭션이 성공한 후 'SubmissionRequested' 이벤트를 발행합니다.
       (규칙 준수: db 인자 없이 2-인자로 publish를 호출하여 외부 도메인/Notification과 결합도를 낮춥니다.)
    """
    # [비즈니스 룰 방어] 마감일(due_date) 입력이 없으면 발송일 기준 +14일 자동 설정
    if not due_date:
        due_date = datetime.now(timezone.utc) + timedelta(days=14)

    new_log = DataRequestLog(
        requester_user_id=requester_user_id,
        target_supplier_id=target_supplier_id,
        requested_data_type=requested_data_type,
        due_date=due_date,
        submission_status=SubmissionStatus.REQUESTED
    )
    
    try:
        req_log = await create_data_request(db, new_log)

        # 2. 초기 REQUESTED 상태에 대한 히스토리 기록 (transition_submission 대체)
        history = SubmissionStatusHistory(
            request_id=req_log.request_id,
            from_status=None,
            to_status=SubmissionStatus.REQUESTED.value,
            actor_id=actor_id,
            reason="최초 공급망 데이터 제출 요청 생성"
        )
        db.add(history)

        # [E-2 비즈니스 룰] supplier_onboarding.last_invited_at 동시 갱신
        # Rule 6(도메인 간 모델 직접 import 금지) 준수를 위해 Raw SQL 사용
        await db.execute(
            text("UPDATE supplier_onboarding SET last_invited_at = :now WHERE supplier_id = :supplier_id"),
            {"now": datetime.now(timezone.utc), "supplier_id": target_supplier_id}
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
    status_event = SubmissionStatusChangedEvent(
        request_id=req_log.request_id,
        from_status=None,
        to_status=SubmissionStatus.REQUESTED.value,
        event_name="SubmissionStatusChanged"
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
    batch_id: Optional[uuid.UUID] = None,
    file_urls: Optional[list[str]] = None,
    confirmed_fields: Optional[dict] = None
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
        if not file_urls and not confirmed_fields:
            raise ValueError("제출 완료(SUBMITTED) 상태로 전이하려면 파일(file_urls)이 첨부되거나 폼 입력값(confirmed_fields)이 있어야 합니다.")

        # [필수 검증] 완성도 100% 미만 물리 차단
        req_log_before = await get_data_request(db, request_id)
        if req_log_before:
            stmt = select(DataCompletenessStatus).where(
                DataCompletenessStatus.entity_type == 'supplier',
                DataCompletenessStatus.entity_id == req_log_before.target_supplier_id
            )
            result = await db.execute(stmt)
            comp_status = result.scalar_one_or_none()
            if not comp_status or (comp_status.completion_rate or 0) < 100:
                raise ValueError("필수 입력 항목이 100% 작성되지 않아 제출할 수 없습니다.")

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
        mode = "file" if file_urls else "form"
        await publish("SubmissionCompleted", dataclasses.asdict(SubmissionCompletedEvent(
            request_id=request_id,
            batch_id=batch_id,
            submission_mode=mode,
            file_urls=file_urls or [],
            confirmed_fields=confirmed_fields or {},
            event_name="SubmissionCompleted"
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

@trace_tool("get_submission_completeness")
async def get_submission_completeness(db: AsyncSession, request_id: uuid.UUID) -> Optional[DataCompletenessStatus]:
    """
    [조회 도구] 제출 건의 대상 협력사 기준 데이터 완성도 조회
    """
    req_log = await get_data_request(db, request_id)
    if not req_log or not req_log.target_supplier_id:
        return None
    return await get_completeness_by_supplier(db, req_log.target_supplier_id)

@trace_tool("get_supplier_submission_timeline")
async def get_supplier_submission_timeline(db: AsyncSession, supplier_id: uuid.UUID) -> list[SubmissionStatusHistory]:
    """
    [조회 도구] 특정 협력사의 모든 데이터 제출 이력 타임라인 조회
    """
    return await get_timeline_by_supplier(db, supplier_id)


@trace_tool("get_evidence_urls_dto")
async def get_evidence_urls_dto(db: AsyncSession, supplier_id: uuid.UUID) -> list[dict]:
    """증빙 URL DTO 조회 헬퍼"""
    stmt = text("SELECT document_id, file_url, file_name, doc_category FROM submission_documents WHERE supplier_id = :supplier_id")
    result = await db.execute(stmt, {"supplier_id": str(supplier_id)})
    return [dict(r._mapping) for r in result.fetchall()]
