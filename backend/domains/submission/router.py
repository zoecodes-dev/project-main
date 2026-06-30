import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db
from backend.infrastructure.acl import (
    _EXEMPT_ROLES,
    get_supplier_id_for_user,
    get_accessible_supplier_ids,
    require_supplier_self_or_connected,
)
from backend.domains.submission.models import (
    SubmissionStatus,
    DataRequestCreateRequest,
    DataRequestResponse,
    SubmitDataRequest,
    ActionDataRequest,
    CompletenessResponse,
    TimelineHistoryResponse
)
from backend.domains.submission.service import (
    create_and_request_submission,
    get_submission_detail,
    update_submission_status,
    get_submissions_list,
    get_submission_completeness,
    get_supplier_submission_timeline,
    check_and_record_pipeline_trigger,
    send_overdue_reminders,
    list_submissions_for_tenant,
    count_submissions_for_tenant,
    get_submission_detail_for_tenant,
    list_ai_extractions,
)
from backend.domains.submission.models import (
    SubmissionBriefOut,
    SubmissionDetailOut,
    SubmissionActionIn,
    ExtractionResultOut,
)

from backend.domains.submission import repository as submission_repo
from backend.agents.graph import create_batch
from backend.infrastructure.queue import (
    enqueue,
    DOCUMENT_PARSE_QUEUE,
    BATCH_PIPELINE_QUEUE,
)

router = APIRouter(prefix="/data-requests", tags=["Submission"])

@router.get("", response_model=List[DataRequestResponse])
async def list_data_requests_endpoint(
    supplier_id: Optional[uuid.UUID] = Query(None, description="협력사 ID 필터"),
    status: Optional[SubmissionStatus] = Query(None, description="제출 상태 필터"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    [API] GET /data-requests
    데이터 제출 요청 목록을 조건부로 필터링하고 페이징하여 조회합니다.

    [ACL] 협력사 역할은 자기 supplier_id로 자동 제한된다.
      - supplier_id 미제공 시: 자기 데이터만 조회.
      - supplier_id 제공 시: 접근 가능한 협력사인지 검증 후 403 또는 통과.
    원청/관리자/감사자는 필터 그대로 통과.
    """
    if current_user.role not in _EXEMPT_ROLES:
        my_supplier_id = await get_supplier_id_for_user(current_user.user_id, db)
        if my_supplier_id is not None:
            if supplier_id is None:
                supplier_id = my_supplier_id
            else:
                accessible = await get_accessible_supplier_ids(my_supplier_id, db)
                if supplier_id not in accessible:
                    raise HTTPException(
                        status_code=403,
                        detail="해당 협력사 데이터에 접근 권한이 없습니다.",
                    )

    return await get_submissions_list(
        db=db, supplier_id=supplier_id, status=status.value if status else None, skip=skip, limit=limit
    )

@router.post("", response_model=DataRequestResponse, status_code=status.HTTP_201_CREATED)
# [REVERT-NON-SUPPLIER:BEGIN] supplier 외(submission) — current_user 의존 + requester/actor 토큰 추론(프론트 발송 배선용).
#   최종작업 시 아래 인라인 [REVERT-NON-SUPPLIER] 줄들을 원복: current_user 파라미터 제거,
#   requester=req.requester_user_id / actor=req.actor_id 직접 사용.
async def create_data_request_endpoint(
    req: DataRequestCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),  # [REVERT-NON-SUPPLIER] 이 줄 제거
):
    """
    [API] POST /data-requests
    새로운 공급망 데이터 제출 요청을 생성하고, 초기 상태(submission_requested)로 설정합니다.
    - 비즈니스 로직(service.py)에 처리를 위임하여 컨트롤러 역할을 수행합니다.
    - requester_user_id/actor_id 미제공 시 토큰의 현재 사용자로 자동 채움(프론트는 대상·유형만 보내면 됨).
    - 도메인 계층에서 발생한 ValueError(무결성 위반 등)를 HTTP 422 상태 코드로 매핑한다.
    """
    requester = req.requester_user_id or current_user.user_id  # [REVERT-NON-SUPPLIER] 원복: requester = req.requester_user_id
    actor = req.actor_id or current_user.user_id  # [REVERT-NON-SUPPLIER] 원복: actor = req.actor_id
    try:
        return await create_and_request_submission(
            db=db,
            requester_user_id=requester,
            target_supplier_id=req.target_supplier_id,
            requested_data_type=req.requested_data_type,
            due_date=req.due_date,
            actor_id=actor,
        )
    # [REVERT-NON-SUPPLIER:END]
    except ValueError as e:
        # 비즈니스 규칙 위반 또는 DB 참조 무결성 위반 시 422 반환
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="서버 내부 오류가 발생했습니다.")

# [REVERT-NON-SUPPLIER:BEGIN] HITL 협력사 승인 — AI 추출 목록(입력+AI분석+신뢰도). /{request_id}보다 먼저 등록.
@router.get("/ai-extractions")
async def list_ai_extractions_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """협력사 자료요청 AI 파싱 결과(parsed_fields + confidence) 목록. HITL 검토·승인용."""
    return await list_ai_extractions(db, current_user.tenant_id)
# [REVERT-NON-SUPPLIER:END]


@router.get("/{request_id}", response_model=DataRequestResponse)
async def get_data_request_endpoint(request_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    [API] GET /data-requests/{request_id}
    단건 데이터 제출 요청 내역을 상세 조회합니다.
    - 존재하지 않는 request_id로 접근할 경우 명시적으로 HTTP 404를 반환합니다.
    """
    log_record = await get_submission_detail(db, request_id)
    if not log_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data request not found")
    return log_record

async def _handle_status_update(db: AsyncSession, request_id: uuid.UUID, to_status: SubmissionStatus, req, **kwargs):
    try:
        return await update_submission_status(
            db=db, request_id=request_id, to_status=to_status,
            actor_id=req.actor_id, **kwargs
        )
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        if "100% 작성되지 않아" in str(e):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="서버 내부 오류가 발생했습니다.")

#   업로드된 파일은 이미 submission_documents에 행으로 존재한다는 전제
#   (file_url/file_type 등은 별도 파일 저장 흐름이 채움). 이 엔드포인트는
#   "이 문서를 파싱 큐에 태운다"는 트리거 역할.
@router.post("/{request_id}/documents/{document_id}/parse", status_code=status.HTTP_202_ACCEPTED)
async def trigger_document_parse_endpoint(
    request_id: uuid.UUID,
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    [API] POST /data-requests/{request_id}/documents/{document_id}/parse
    협력사가 업로드한 문서를 파싱 큐(document_parse_queue)에 태운다.
    파싱은 워커가 비동기로 수행하므로 즉시 202 + job_id를 반환한다.
 
    멱등성: job_id를 document_id 기반으로 고정 → 같은 문서 중복 enqueue 방어.
    """
    job_id = await enqueue(
        DOCUMENT_PARSE_QUEUE,
        "process_document_parse",
        job_id=f"document_parse:{document_id}",   # 멱등성 키 (queue.py가 _job_id로 매핑)
        document_id=str(document_id),
        request_id=str(request_id),
    )
    return {"status": "accepted", "job_id": job_id, "document_id": str(document_id)}


@router.post("/{request_id}/submit", response_model=DataRequestResponse)
async def submit_data_request_endpoint(request_id: uuid.UUID, req: SubmitDataRequest, db: AsyncSession = Depends(get_db)):
    """
    [API] POST /data-requests/{request_id}/submit
    협력사가 작성을 마치고 최종 제출을 확정합니다.
    batch를 자동 생성하고 파이프라인 큐에 적재합니다.
    """
    try:
        batch_id_str = await create_batch(db, str(req.product_id), req.destination)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"배치 생성 실패: {e}")

    req_log = await _handle_status_update(
        db, request_id, SubmissionStatus.SUBMITTED, req,
        batch_id=uuid.UUID(batch_id_str), file_urls=req.file_urls, confirmed_fields=req.confirmed_fields
    )
    # 파이프라인은 승인(approve) 후에 시작 — 여기서는 배치 생성만 하고 enqueue 하지 않음
    return req_log

@router.post("/{request_id}/approve", response_model=DataRequestResponse)
async def approve_data_request_endpoint(request_id: uuid.UUID, req: ActionDataRequest, db: AsyncSession = Depends(get_db)):
    """
    [API] POST /data-requests/{request_id}/approve
    원청사 담당자가 제출 내역을 승인하고, 파이프라인을 시작합니다.

    submit 시 생성·저장된 batch_id를 조회해 enqueue한다.
    job_id=f"pipeline:{batch_id}" 멱등성 키로 중복 invoke를 방어한다.
    """
    # submit 때 저장된 batch_id 조회
    req_log = await get_submission_detail(db, request_id)
    if not req_log:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data request not found")

    batch_id = req_log.batch_id

    approved = await _handle_status_update(
        db, request_id, SubmissionStatus.APPROVED, req,
        reason=req.reason,
        batch_id=batch_id,  # SubmissionApproved 이벤트에 batch_id가 실림
    )

    # 파이프라인 시작 — batch_id가 있을 때만 (재제출 등 예외 케이스 방어)
    if batch_id:
        from sqlalchemy import text as _text
        row = (await db.execute(
            _text("SELECT product_id, destination FROM batches WHERE batch_id = :bid"),
            {"bid": batch_id},
        )).one_or_none()
        if row:
            should_run = await check_and_record_pipeline_trigger(db, request_id, batch_id)
            if should_run:
                await enqueue(
                    BATCH_PIPELINE_QUEUE,
                    "start_batch_pipeline",
                    job_id=f"pipeline:{batch_id}",
                    batch_id=str(batch_id),
                    product_id=str(row.product_id),
                    destination=row.destination,
                )

    return approved

@router.post("/{request_id}/reject", response_model=DataRequestResponse, include_in_schema=False)
async def reject_data_request_endpoint(request_id: uuid.UUID, req: ActionDataRequest, db: AsyncSession = Depends(get_db)):
    """
    [API] POST /data-requests/{request_id}/reject
    데이터 신뢰성 부족 등의 사유로 제출건을 반려합니다.
    """
    if not req.reason:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="반려 시 사유(reason)는 필수입니다.")
    return await _handle_status_update(
        db, request_id, SubmissionStatus.REJECTED, req,
        reason=req.reason
    )

@router.post("/{request_id}/rework", response_model=DataRequestResponse, include_in_schema=False)
async def rework_data_request_endpoint(request_id: uuid.UUID, req: ActionDataRequest, db: AsyncSession = Depends(get_db)):
    """
    [API] POST /data-requests/{request_id}/rework
    협력사에게 제출 데이터 보완을 요청합니다 (포털 재수정 오픈).
    - 상태 전이: submission_review -> submission_rework
    """
    if not req.reason:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="보완 요청 시 사유(reason)는 필수입니다.")
    return await _handle_status_update(
        db, request_id, SubmissionStatus.REWORK, req,
        reason=req.reason
    )

@router.get("/{request_id}/completeness", response_model=CompletenessResponse, include_in_schema=False)
async def get_data_request_completeness_endpoint(request_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    [API] GET /data-requests/{request_id}/completeness
    해당 데이터 요청의 대상 협력사 데이터 완성도(누락 필드 및 비율)를 조회합니다.
    """
    comp_status = await get_submission_completeness(db, request_id)
    if not comp_status:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="해당 요청에 대한 완성도 정보가 존재하지 않습니다.")
    return comp_status

@router.get(
    "/suppliers/{supplier_id}/submission-timeline",
    response_model=List[TimelineHistoryResponse],
    include_in_schema=False,
    dependencies=[Depends(require_supplier_self_or_connected("supplier_id"))],
)
async def get_supplier_timeline_endpoint(supplier_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    [API] GET /data-requests/suppliers/{supplier_id}/submission-timeline
    (E-7 규격 대응) 특정 협력사의 데이터 발송~승인/보완/재제출 시간순 타임라인을 조회합니다.

    [ACL] 협력사는 자기 자신 또는 직접 연결된 노드의 타임라인만 조회 가능.
    """
    return await get_supplier_submission_timeline(db, supplier_id)


@router.post("/send-reminders", include_in_schema=False)
async def send_reminders_endpoint(db: AsyncSession = Depends(get_db)):
    """
    [API] POST /data-requests/send-reminders
    기한이 지났는데 아직 미제출인 협력사에게 수동으로 독촉 알림을 발송합니다.
    원청 담당자가 긴급하게 즉시 발송할 때 사용합니다.
    """
    count = await send_overdue_reminders(db)
    return {"status": "ok", "reminded_count": count}


# ── §4.1 /submissions 라우터 (프론트 계약 응답 형태) ──────────────────────
submissions_router = APIRouter(prefix="/submissions", tags=["Submissions"])


@submissions_router.get("", response_model=list[SubmissionBriefOut])
async def list_submissions(
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] GET /submissions — 제출 검토 목록 + X-Total-Count. tenant_id 격리."""
    from backend.infrastructure.pagination import set_total_count
    items = await list_submissions_for_tenant(db, current_user.tenant_id, skip, limit)
    total = await count_submissions_for_tenant(db, current_user.tenant_id)
    set_total_count(response, total)
    return items


@submissions_router.get("/{submission_id}", response_model=SubmissionDetailOut)
async def get_submission(
    submission_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] GET /submissions/{submissionId} — 제출 상세. tenant 격리."""
    detail = await get_submission_detail_for_tenant(db, submission_id, current_user.tenant_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Submission not found")
    return detail


@submissions_router.patch("/{submission_id}/approve")
async def approve_submission(
    submission_id: uuid.UUID,
    body: SubmissionActionIn,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] PATCH /submissions/{submissionId}/approve — 승인 + 파이프라인 enqueue."""
    from sqlalchemy import text as _text
    req_log = await get_submission_detail(db, submission_id)
    if not req_log:
        raise HTTPException(status_code=404, detail="Submission not found")

    batch_id = req_log.batch_id
    approved = await _handle_status_update(
        db, submission_id, SubmissionStatus.APPROVED,
        type("Req", (), {"actor_id": current_user.user_id, "reason": body.reason})(),
        reason=body.reason, batch_id=batch_id,
    )
    if batch_id:
        row = (await db.execute(
            _text("SELECT product_id, destination FROM batches WHERE batch_id = :bid"),
            {"bid": batch_id},
        )).one_or_none()
        if row:
            should_run = await check_and_record_pipeline_trigger(db, submission_id, batch_id)
            if should_run:
                await enqueue(
                    BATCH_PIPELINE_QUEUE,
                    "start_batch_pipeline",
                    job_id=f"pipeline:{batch_id}",
                    batch_id=str(batch_id),
                    product_id=str(row.product_id),
                    destination=row.destination,
                )
    return approved


@submissions_router.patch("/{submission_id}/rework")
async def rework_submission(
    submission_id: uuid.UUID,
    body: SubmissionActionIn,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] PATCH /submissions/{submissionId}/rework — 보완 요청."""
    if not body.reason:
        raise HTTPException(status_code=422, detail="보완 요청 시 사유(reason)는 필수입니다.")
    return await _handle_status_update(
        db, submission_id, SubmissionStatus.REWORK,
        type("Req", (), {"actor_id": current_user.user_id, "reason": body.reason})(),
        reason=body.reason,
    )


submission_documents_router = APIRouter(prefix="/submission-documents", tags=["Submission"])


@submission_documents_router.get(
    "/{document_id}/extraction-result",
    response_model=ExtractionResultOut,
)
async def get_extraction_result(
    document_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    [API] GET /submission-documents/{document_id}/extraction-result

    특정 문서의 AI 파싱 결과(최신 1건)를 반환한다.
    tenant 격리: 요청자의 tenant_id가 해당 문서 supplier의 tenant_id와 일치해야 한다.
    미일치 또는 결과 없음 모두 404 — 타 tenant 문서 존재 여부를 노출하지 않는다.
    """
    row = await submission_repo.get_latest_extraction_result_by_document_id(
        db, document_id, current_user.tenant_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Extraction result not found")
    return row


@submission_documents_router.post("/{document_id}/confirm")
async def confirm_extraction_endpoint(
    document_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    [API] POST /submission-documents/{document_id}/confirm

    해당 document_id 최신 추출결과를 검토 확정(supplier_confirmed=TRUE)한다.
    - tenant 격리: SQL 단계에서 document → supplier → tenant_id 경로로 검증.
    - 멱등 재호출: 이미 확정된 경우 confirmed_at을 갱신하지 않고 200 반환.
    - tenant 불일치 또는 document 없음 모두 404 (존재 여부 노출하지 않음).
    """
    row = await submission_repo.confirm_extraction_result(
        db, document_id, current_user.tenant_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Extraction result not found")
    return {
        "document_id": str(row["document_id"]),
        "supplier_confirmed": row["supplier_confirmed"],
        "confirmed_at": row["confirmed_at"],
    }


@submissions_router.patch("/{submission_id}/reject")
async def reject_submission(
    submission_id: uuid.UUID,
    body: SubmissionActionIn,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[API] PATCH /submissions/{submissionId}/reject — 반려."""
    if not body.reason:
        raise HTTPException(status_code=422, detail="반려 시 사유(reason)는 필수입니다.")
    return await _handle_status_update(
        db, submission_id, SubmissionStatus.REJECTED,
        type("Req", (), {"actor_id": current_user.user_id, "reason": body.reason})(),
        reason=body.reason,
    )
