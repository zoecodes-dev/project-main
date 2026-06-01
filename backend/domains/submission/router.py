import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import get_db
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
    get_supplier_submission_timeline
)

router = APIRouter(prefix="/data-requests", tags=["Submission"])

@router.get("", response_model=List[DataRequestResponse])
async def list_data_requests_endpoint(
    supplier_id: Optional[uuid.UUID] = Query(None, description="협력사 ID 필터"),
    status: Optional[SubmissionStatus] = Query(None, description="제출 상태 필터"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """
    [API] GET /data-requests
    데이터 제출 요청 목록을 조건부로 필터링하고 페이징하여 조회합니다.
    - UI에서 협력사별(supplier_id) 또는 상태별(status)로 목록을 조회할 수 있도록 Query 파라미터를 제공합니다.
    - 인프라 계층(get_db)으로부터 데이터베이스 비동기 세션을 의존성 주입(Depends) 받습니다.
    """
    return await get_submissions_list(
        db=db, supplier_id=supplier_id, status=status.value if status else None, skip=skip, limit=limit
    )

@router.post("", response_model=DataRequestResponse, status_code=status.HTTP_201_CREATED)
async def create_data_request_endpoint(req: DataRequestCreateRequest, db: AsyncSession = Depends(get_db)):
    """
    [API] POST /data-requests
    새로운 공급망 데이터 제출 요청을 생성하고, 초기 상태(submission_requested)로 설정합니다.
    - 비즈니스 로직(service.py)에 처리를 위임하여 컨트롤러 역할을 수행합니다.
    - 도메인 계층에서 발생한 ValueError(무결성 위반 등)를 HTTP 422 상태 코드로 매핑하여
      도메인 계층이 웹 프레임워크(FastAPI)에 의존하지 않고 안전하게 에러를 처리하도록 설계되었습니다.
    """
    try:
        return await create_and_request_submission(
            db=db,
            requester_user_id=req.requester_user_id,
            target_supplier_id=req.target_supplier_id,
            requested_data_type=req.requested_data_type,
            due_date=req.due_date,
            actor_id=req.actor_id
        )
    except ValueError as e:
        # 비즈니스 규칙 위반 또는 DB 참조 무결성 위반 시 422 반환
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="서버 내부 오류가 발생했습니다.")

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

@router.post("/{request_id}/submit", response_model=DataRequestResponse)
async def submit_data_request_endpoint(request_id: uuid.UUID, req: SubmitDataRequest, db: AsyncSession = Depends(get_db)):
    """
    [API] POST /data-requests/{request_id}/submit
    협력사가 작성을 마치고 최종 제출을 확정합니다.
    """
    return await _handle_status_update(
        db, request_id, SubmissionStatus.SUBMITTED, req,
        batch_id=req.batch_id, file_urls=req.file_urls, confirmed_fields=req.confirmed_fields
    )

@router.post("/{request_id}/approve", response_model=DataRequestResponse)
async def approve_data_request_endpoint(request_id: uuid.UUID, req: ActionDataRequest, db: AsyncSession = Depends(get_db)):
    """
    [API] POST /data-requests/{request_id}/approve
    원청사 담당자가 제출 내역을 승인합니다.
    """
    return await _handle_status_update(
        db, request_id, SubmissionStatus.APPROVED, req,
        reason=req.reason
    )

@router.post("/{request_id}/reject", response_model=DataRequestResponse)
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

@router.post("/{request_id}/rework", response_model=DataRequestResponse)
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

@router.get("/{request_id}/completeness", response_model=CompletenessResponse)
async def get_data_request_completeness_endpoint(request_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    [API] GET /data-requests/{request_id}/completeness
    해당 데이터 요청의 대상 협력사 데이터 완성도(누락 필드 및 비율)를 조회합니다.
    """
    comp_status = await get_submission_completeness(db, request_id)
    if not comp_status:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="해당 요청에 대한 완성도 정보가 존재하지 않습니다.")
    return comp_status

@router.get("/suppliers/{supplier_id}/submission-timeline", response_model=List[TimelineHistoryResponse])
async def get_supplier_timeline_endpoint(supplier_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    [API] GET /data-requests/suppliers/{supplier_id}/submission-timeline
    (E-7 규격 대응) 특정 협력사의 데이터 발송~승인/보완/재제출 시간순 타임라인을 조회합니다.
    """
    return await get_supplier_submission_timeline(db, supplier_id)
