"""
domains/files/service.py  (담당: 팀원 B / 공통)

공통 파일 업로드(§0.8) 비즈니스 로직. 검증 → S3 업로드 → 메타 영속 → commit.
- 커밋은 service 일원화(router는 커밋 안 함). S3 업로드 성공 후 DB 커밋.
- 허용 확장자/용량 가드. 위반 시 ValueError(라우터가 4xx로 변환).
"""
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.files.repository import FileRepository
from backend.infrastructure import storage

# §0.8 허용 확장자 / 최대 50MB
ALLOWED_EXT = {"pdf", "xlsx", "xls", "csv", "docx", "doc", "png", "jpg", "jpeg"}
MAX_SIZE_BYTES = 50 * 1024 * 1024


def _ext(file_name: str) -> str:
    return file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""


def _validate(file_name: str, size: int) -> None:
    ext = _ext(file_name)
    if ext not in ALLOWED_EXT:
        raise ValueError(
            f"허용되지 않은 확장자입니다(.{ext}). 허용: {', '.join(sorted(ALLOWED_EXT))}"
        )
    if size > MAX_SIZE_BYTES:
        raise ValueError("파일 용량이 50MB를 초과합니다.")
    if size == 0:
        raise ValueError("빈 파일은 업로드할 수 없습니다.")


async def upload_file(
    db: AsyncSession,
    *,
    file_name: str,
    data: bytes,
    content_type: Optional[str],
    context: Optional[str],
    tenant_id: Optional[uuid.UUID],
    uploaded_by: Optional[uuid.UUID],
) -> dict:
    """파일 1건 업로드. 검증 → S3 put → files insert → commit. 응답(snake_case) 반환."""
    size = len(data)
    _validate(file_name, size)

    # S3 키: 테넌트별 네임스페이스 + 충돌 방지 uuid. 영구 URL 아님(키만 보관).
    key = f"files/{tenant_id or 'common'}/{uuid.uuid4()}/{file_name}"
    await storage.upload_bytes(key, data, content_type)

    repo = FileRepository(db)
    obj = await repo.create(
        tenant_id=tenant_id,
        file_name=file_name,
        content_type=content_type,
        size_bytes=size,
        s3_key=key,
        context=context,
        uploaded_by=uploaded_by,
    )
    await db.commit()

    url = await storage.generate_presigned_url(key)
    return {
        "file_id": str(obj.file_id),
        "file_name": obj.file_name,
        "size_bytes": obj.size_bytes,
        "content_type": obj.content_type,
        "url": url,
    }


async def get_file_meta(
    db: AsyncSession, file_id: uuid.UUID, tenant_id: Optional[uuid.UUID]
) -> Optional[dict]:
    """메타 + 서명된 downloadUrl. 없거나 타 테넌트면 None(→404)."""
    obj = await FileRepository(db).get(file_id, tenant_id)
    if obj is None:
        return None
    download_url = await storage.generate_presigned_url(obj.s3_key)
    return {
        "file_id": str(obj.file_id),
        "file_name": obj.file_name,
        "size_bytes": obj.size_bytes,
        "content_type": obj.content_type,
        "download_url": download_url,
    }


# [MARKER:BEGIN] context별 파일 목록(환경성적서 첨부 조회). files=공통(비-supplier) 도메인.
# async def list_files(
#     db: AsyncSession, context: str, tenant_id: Optional[uuid.UUID]
# ) -> list[dict]:
#     """context 태그로 업로드된 파일 목록(메타). 환경성적서 첨부 표시용."""
#     objs = await FileRepository(db).list_by_context(context, tenant_id)
#     return [
#         {
#             "file_id": str(o.file_id),
#             "file_name": o.file_name,
#             "size_bytes": o.size_bytes,
#             "content_type": o.content_type,
#             "context": o.context,
#             "created_at": o.created_at.isoformat() if o.created_at else None,
#         }
#         for o in objs
#     ]
# [MARKER:END]


async def delete_file(
    db: AsyncSession, file_id: uuid.UUID, tenant_id: Optional[uuid.UUID]
) -> bool:
    """S3 객체 + 메타 삭제. 없거나 타 테넌트면 False(→404)."""
    repo = FileRepository(db)
    obj = await repo.get(file_id, tenant_id)
    if obj is None:
        return False
    await storage.delete_object(obj.s3_key)
    await repo.delete(obj)
    await db.commit()
    return True
