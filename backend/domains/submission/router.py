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
    DataRequestStatusUpdateRequest
)
from backend.domains.submission.service import create_and_request_submission, get_submission_detail, update_submission_status, get_submissions_list

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
    새로운 공급망 데이터 제출 요청을 생성하고, 초기 상태 전이(PENDING -> REQUESTED)를 수행합니다.
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

@router.patch("/{request_id}/status", response_model=DataRequestResponse)
async def update_data_request_status_endpoint(request_id: uuid.UUID, req: DataRequestStatusUpdateRequest, db: AsyncSession = Depends(get_db)):
    """
    [API] PATCH /data-requests/{request_id}/status
    특정 데이터 제출 요청건의 상태를 명시적으로 전이합니다.
    - 단순한 DB UPDATE 대신 비즈니스 규칙(상태 머신 전이 매트릭스)을 엄격히 통과하도록 강제합니다.
    - 잘못된 상태 전이 시도는 422 에러로 튕겨내며, 상태 갱신이 성공하면 
      목표 상태에 맞는 이벤트(SubmissionStarted/Completed 등)가 정상적으로 발행됩니다.
    """
    try:
        log_record = await update_submission_status(
            db=db,
            request_id=request_id,
            to_status=req.to_status,
            actor_id=req.actor_id,
            reason=req.reason,
            batch_id=req.batch_id,
            file_urls=req.file_urls
        )
        return log_record
    except ValueError as e:
        # 에러 메시지에 'not found'가 포함되면 404, 허용되지 않은 상태 전이 등의 규칙 위반이면 422 반환
        if "not found" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="서버 내부 오류가 발생했습니다.")
