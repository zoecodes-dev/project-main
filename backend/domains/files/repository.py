"""
domains/files/repository.py  (담당: 팀원 B / 공통)

files 테이블 DB 접근. 커밋하지 않는다(flush만) — 커밋은 service 일원화.
"""
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.files.models import FileObject


class FileRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        tenant_id: Optional[uuid.UUID],
        file_name: str,
        content_type: Optional[str],
        size_bytes: Optional[int],
        s3_key: str,
        context: Optional[str],
        uploaded_by: Optional[uuid.UUID],
    ) -> FileObject:
        obj = FileObject(
            tenant_id=tenant_id,
            file_name=file_name,
            content_type=content_type,
            size_bytes=size_bytes,
            s3_key=s3_key,
            context=context,
            uploaded_by=uploaded_by,
        )
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def get(
        self, file_id: uuid.UUID, tenant_id: Optional[uuid.UUID] = None
    ) -> Optional[FileObject]:
        """단건 조회. tenant_id 지정 시 소유 테넌트만(§0.2) — 남의 것은 None(→404)."""
        stmt = select(FileObject).where(FileObject.file_id == file_id)
        if tenant_id is not None:
            stmt = stmt.where(FileObject.tenant_id == tenant_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    # [REVERT-NON-SUPPLIER:BEGIN] context별 파일 목록(환경성적서 첨부 조회용). files=공통(비-supplier) 도메인.
    async def list_by_context(
        self, context: str, tenant_id: Optional[uuid.UUID] = None
    ):
        """context 태그로 파일 목록 조회(예: 'carbon-epd:<supplierId>'). 최신순."""
        stmt = select(FileObject).where(FileObject.context == context)
        if tenant_id is not None:
            stmt = stmt.where(FileObject.tenant_id == tenant_id)
        stmt = stmt.order_by(FileObject.created_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
    # [REVERT-NON-SUPPLIER:END]

    async def delete(self, obj: FileObject) -> None:
        await self.db.delete(obj)
        await self.db.flush()
