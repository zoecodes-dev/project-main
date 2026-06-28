"""
domains/files/router.py  (담당: 팀원 B / 공통)

공통 파일 업로드(§0.8). 첨부가 있는 화면(자료제출·실사·시정·온보딩)이 공용으로 쓴다.
- POST /files        multipart(file + optional context) → { fileId, fileName, sizeBytes, contentType, url }
- GET  /files/{id}   → 메타 + 서명된 downloadUrl
- DELETE /files/{id} → 204
얇은 라우팅만. 검증·S3·커밋은 service.
"""
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db
from backend.domains.files import service

router = APIRouter(prefix="/files", tags=["Files"])


@router.post("", status_code=201)
async def upload_file_endpoint(
    file: UploadFile = File(...),
    context: str | None = Form(None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """파일 업로드. 허용 확장자/50MB 위반 시 422."""
    data = await file.read()
    try:
        return await service.upload_file(
            db,
            file_name=file.filename or "unnamed",
            data=data,
            content_type=file.content_type,
            context=context,
            tenant_id=current_user.tenant_id,
            uploaded_by=current_user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# [MARKER:BEGIN] context별 파일 목록 — 환경성적서 첨부 조회용. files=공통(비-supplier).
# @router.get("")
# async def list_files_endpoint(
#     context: str,
#     current_user: CurrentUser = Depends(get_current_user),
#     db: AsyncSession = Depends(get_db),
# ):
#     """context 태그(예: 'carbon-epd:<supplierId>')로 업로드된 파일 목록 조회."""
#     return await service.list_files(db, context, current_user.tenant_id)
# [MARKER:END]


@router.get("/{file_id}")
async def get_file_endpoint(
    file_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """파일 메타 + 서명된 downloadUrl. 없거나 타 테넌트면 404(존재 은닉)."""
    meta = await service.get_file_meta(db, file_id, current_user.tenant_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="File not found")
    return meta


@router.delete("/{file_id}", status_code=204)
async def delete_file_endpoint(
    file_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """파일 삭제(S3 + 메타). 없거나 타 테넌트면 404."""
    ok = await service.delete_file(db, file_id, current_user.tenant_id)
    if not ok:
        raise HTTPException(status_code=404, detail="File not found")
    return None
